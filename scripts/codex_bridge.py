"""Serve the host's logged-in Codex CLI as a minimal Chat Completions endpoint.

Lets LLM_PROVIDER=codex use a ChatGPT plan instead of an API key, the way
local-agent tools drive `codex exec`. Run it on the machine where `codex login`
was done; the backend container reaches it through host.docker.internal.
Only text chat is supported: requests with `tools` are rejected so the backend
falls back to its tool-free path. Standard library only.

    python3 scripts/codex_bridge.py [--port 11535] [--model MODEL] [--effort low]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREAMBLE = (
    "You are the text-generation backend of a language-learning app. "
    "Do not run commands, read files, or use any tools. "
    "Follow the SYSTEM instructions, read the CONVERSATION, and write only the "
    "assistant's next reply, with no preamble or commentary."
)


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return "" if content is None else str(content)


def build_prompt(messages: list[dict]) -> str:
    system = [message_text(m.get("content")) for m in messages if m.get("role") == "system"]
    turns = [
        f"[{m.get('role', 'user').upper()}]\n{message_text(m.get('content'))}"
        for m in messages
        if m.get("role") != "system"
    ]
    sections = [PREAMBLE]
    if system:
        sections.append("=== SYSTEM ===\n" + "\n\n".join(system))
    if turns:
        sections.append("=== CONVERSATION ===\n" + "\n\n".join(turns))
    sections.append("=== YOUR REPLY (assistant) ===")
    return "\n\n".join(sections)


class CodexError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Bridge:
    def __init__(self, codex: str, model: str, effort: str, timeout: float, concurrency: int):
        self.codex = codex
        self.model = model
        self.effort = effort
        self.timeout = timeout
        self.slots = threading.Semaphore(concurrency)

    def complete(self, messages: list[dict], model: str | None) -> tuple[str, dict]:
        args = [
            self.codex, "exec", "--json", "--skip-git-repo-check", "--ephemeral",
            "--ignore-user-config", "--sandbox", "read-only",
            "-c", "shell_environment_policy.inherit=\"none\"",
        ]
        if self.effort:
            args += ["-c", f'model_reasoning_effort="{self.effort}"']
        chosen = model if model and model not in ("codex", "default") else self.model
        if chosen:
            args += ["--model", chosen]
        prompt = build_prompt(messages)
        with self.slots, tempfile.TemporaryDirectory(prefix="codex-bridge-") as workdir:
            try:
                proc = subprocess.run(
                    args, input=prompt, capture_output=True, text=True,
                    cwd=workdir, timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise CodexError(504, "codex request timed out") from exc
        text, usage, error = "", {}, None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            item = event.get("item") or {}
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text", "")
            elif kind == "turn.completed":
                usage = event.get("usage") or {}
            elif kind in ("turn.failed", "error"):
                error = (event.get("error") or {}).get("message") or event.get("message")
        if error or proc.returncode != 0:
            detail = error or proc.stderr.strip()[-500:] or f"codex exited {proc.returncode}"
            lowered = detail.lower()
            if "usage limit" in lowered or "rate limit" in lowered:
                raise CodexError(429, f"codex rate limit exceeded: {detail}")
            if "login" in lowered or "unauthorized" in lowered or "401" in lowered:
                raise CodexError(401, f"codex is not logged in (run `codex login`): {detail}")
            raise CodexError(502, f"codex error: {detail}")
        if not text.strip():
            raise CodexError(502, "codex returned an empty reply")
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0) + int(
            usage.get("reasoning_output_tokens") or 0
        )
        return text, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }


def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("[codex-bridge] " + fmt % args + "\n")

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": {"message": message, "type": "codex_bridge_error"}})

        def do_GET(self):
            if self.path.rstrip("/") in ("/v1/models", "/health"):
                self._json(200, {"object": "list", "data": [{"id": "codex", "object": "model"}]})
            else:
                self._error(404, "not found")

        def do_POST(self):
            if self.path.rstrip("/") != "/v1/chat/completions":
                self._error(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                request = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._error(400, "invalid JSON body")
                return
            if request.get("tools"):
                self._error(400, "tools are not supported by the codex bridge")
                return
            messages = request.get("messages") or []
            started = time.monotonic()
            try:
                text, usage = bridge.complete(messages, request.get("model"))
            except CodexError as exc:
                self.log_message("failed after %.1fs: %s", time.monotonic() - started, exc)
                self._error(exc.status, str(exc))
                return
            self.log_message(
                "ok %.1fs prompt=%d completion=%d",
                time.monotonic() - started, usage["prompt_tokens"], usage["completion_tokens"],
            )
            reply_id = f"chatcmpl-{uuid.uuid4().hex}"
            created = int(time.time())
            model = request.get("model") or "codex"
            if not request.get("stream"):
                self._json(200, {
                    "id": reply_id, "object": "chat.completion", "created": created,
                    "model": model, "usage": usage,
                    "choices": [{
                        "index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                    }],
                })
                return
            # `codex exec --json` reports whole messages, so the reply arrives as one chunk.
            chunk = {"id": reply_id, "object": "chat.completion.chunk", "created": created, "model": model}
            events = [
                {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
                {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {**chunk, "choices": [], "usage": usage},
            ]
            body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11535)
    parser.add_argument("--model", default="", help="Codex model; empty uses the Codex default")
    parser.add_argument("--effort", default="low", help="model_reasoning_effort; empty for default")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--concurrency", type=int, default=3)
    opts = parser.parse_args()
    codex = shutil.which("codex")
    if codex is None:
        sys.exit("codex CLI not found on PATH; install it and run `codex login`")
    bridge = Bridge(codex, opts.model, opts.effort, opts.timeout, opts.concurrency)
    server = ThreadingHTTPServer((opts.host, opts.port), make_handler(bridge))
    print(f"[codex-bridge] serving {codex} on http://{opts.host}:{opts.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
