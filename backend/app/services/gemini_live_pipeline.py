"""Voice conversation through one Gemini Live speech-to-speech session.

Keeps the browser protocol of ConversationPipeline: the frontend does VAD and sends one
WAV per utterance; the backend answers with audio chunks (WAV here) plus the same JSON
events (status, transcript, turn_complete, barge_in, memory_updated, ...). Gemini's own
VAD is disabled and every utterance is sent as one explicit activity, so Gemini's turns
line up with the frontend's.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import time
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from google import genai
from google.genai import types

from app.core.app_logger import get_logger
from app.services.ai_errors import record_ai_failure, record_ai_success
from app.services.conversation_pipeline import (
    ConversationPipeline,
    _build_conversation_system_prompt,
)
from app.services.llm_adapter import LLMToolCall
from app.services.memory_service import build_save_user_memory_tool, execute_save_user_memory
from app.services.tts_service import pcm16_to_wav
from app.utils.db import db_session

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger(__name__)

IN_RATE = 16000
OUT_RATE = 24000
SEND_CHUNK_BYTES = IN_RATE * 2 // 10  # 100 ms per realtime-input message
# Send the first reply audio quickly, then in larger pieces to limit per-chunk decode overhead.
FIRST_AUDIO_FLUSH_BYTES = OUT_RATE * 2 // 4  # 250 ms
AUDIO_FLUSH_BYTES = OUT_RATE * 2  # 1 s
NO_REPLY_TIMEOUT_SECONDS = 20

LIVE_VOICE_ADDENDUM = """
Voice session notes:
- You are speaking aloud in a live voice call; the student hears your voice and does
  not see any text. Never use lists, headings or formatting.
- Speak slowly and clearly enough for a {cefr_level} learner, with natural intonation.
"""


@dataclass
class _LiveTurn:
    turn_id: int
    is_greeting: bool = False
    user_text: str = ""
    model_text: str = ""
    audio: bytearray = field(default_factory=bytearray)
    audio_chunks_sent: int = 0
    output_started: bool = False
    user_sent: bool = False
    finalized: bool = False
    dropped: bool = False
    ignore_next_complete: bool = False
    sent_at: float = field(default_factory=time.monotonic)


def wav_to_pcm16(audio_bytes: bytes) -> bytes:
    """Return mono 16 kHz PCM16 from a WAV upload, downmixing/resampling when needed."""
    with wave.open(io.BytesIO(audio_bytes)) as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"Unsupported WAV sample width: {width * 8} bits")
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    if channels > 1:
        samples = tuple(
            sum(samples[i : i + channels]) // channels for i in range(0, len(samples), channels)
        )
    if rate != IN_RATE and samples:
        ratio = rate / IN_RATE
        count = int(len(samples) / ratio)
        samples = tuple(samples[min(len(samples) - 1, int(i * ratio))] for i in range(count))
    return struct.pack(f"<{len(samples)}h", *samples)


class GeminiLivePipeline(ConversationPipeline):
    """ConversationPipeline variant where Gemini Live does STT, reasoning and speech."""

    def __init__(self, *, api_key: str, model: str, live_voice: str, **kwargs) -> None:
        super().__init__(llm=None, tts=None, stt=None, **kwargs)
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._live_voice = live_voice
        self._session: object | None = None
        self._session_ready = asyncio.Event()
        self._resume_handle: str | None = None
        self._turns: list[_LiveTurn] = []
        self._carried_user_text = ""
        self._closing = False
        self._session_task: asyncio.Task | None = None

    # --- Gemini session ---

    def _live_system_prompt(self) -> str:
        prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
            memory_tools_enabled=self._memory_tools_available and self._user_id is not None,
        )
        return prompt + LIVE_VOICE_ADDENDUM.format(cefr_level=self._prompt_args["cefr_level"])

    def _live_config(self) -> types.LiveConnectConfig:
        tools = None
        if self._memory_tools_available and self._user_id is not None:
            tool = build_save_user_memory_tool(str(self._prompt_args["native_language"]))
            tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters_json_schema=tool.input_schema,
                            behavior=types.Behavior.NON_BLOCKING,
                        )
                    ]
                )
            ]
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=self._live_system_prompt(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._live_voice)
                )
            ),
            input_audio_transcription={},
            output_audio_transcription={},
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
            ),
            # Compression lifts the audio session length cap; resumption survives the
            # periodic connection resets (GoAway) of long sessions.
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
            tools=tools,
        )

    async def _session_loop(self, ws: WebSocket) -> None:
        first = True
        while not self._closing:
            async with self._client.aio.live.connect(
                model=self._model, config=self._live_config()
            ) as session:
                logger.info(
                    "[gemini-live] session connected model=%s resumed=%s",
                    self._model,
                    not first,
                )
                self._session = session
                self._session_ready.set()
                record_ai_success("voice")
                if first:
                    first = False
                    await self._send_opening(session)
                try:
                    await self._receive(session, ws)
                finally:
                    self._session_ready.clear()
                    self._session = None
            if self._closing:
                return
            if not self._resume_handle:
                raise RuntimeError("Gemini Live session ended and cannot be resumed")
            # Turns that were in flight on the old connection will never complete.
            for turn in self._turns:
                if not turn.finalized:
                    await self._finish_turn_ui(ws, turn)
            self._turns.clear()

    async def _send_opening(self, session: object) -> None:
        if self.history:
            await session.send_client_content(
                turns=[
                    types.Content(
                        role="model" if m["role"] == "assistant" else "user",
                        parts=[types.Part(text=m["content"])],
                    )
                    for m in self.history[-20:]
                ],
                turn_complete=False,
            )
        greeting = _LiveTurn(self._next_turn_id(), is_greeting=True)
        self._turns.append(greeting)
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[
                    types.Part(
                        text="[Session started. Greet the student warmly and naturally — one or "
                        "two sentences max — and invite them to speak.]"
                    )
                ],
            ),
            turn_complete=True,
        )

    async def _receive(self, session: object, ws: WebSocket) -> None:
        going_away = False
        while True:
            received = 0
            async for msg in session.receive():
                received += 1
                if msg.session_resumption_update and msg.session_resumption_update.new_handle:
                    self._resume_handle = msg.session_resumption_update.new_handle
                if msg.go_away:
                    logger.info(
                        "[gemini-live] GoAway received, time_left=%s", msg.go_away.time_left
                    )
                    going_away = True
                if msg.usage_metadata:
                    self._pending_saves.append(
                        asyncio.create_task(self._save_live_usage(msg.usage_metadata))
                    )
                if msg.tool_call:
                    await self._handle_tool_call(session, ws, msg.tool_call)
                if msg.server_content:
                    await self._handle_server_content(ws, msg.server_content)
            if received == 0:
                logger.info("[gemini-live] connection closed by server")
                return
            if going_away and not any(t.output_started for t in self._turns):
                return

    async def _handle_server_content(self, ws: WebSocket, sc: types.LiveServerContent) -> None:
        if sc.input_transcription and sc.input_transcription.text:
            target = next((t for t in reversed(self._turns) if not t.is_greeting), None)
            if target is not None:
                target.user_text += sc.input_transcription.text
        head = self._turns[0] if self._turns else None
        if head is None:
            return
        if sc.interrupted and not head.dropped:
            # Gemini cut off a reply we had not yet marked stale: it belonged to an older
            # activity. Discard it and keep this turn open for the real answer.
            logger.info("[gemini-live] interrupted before turn %s was marked", head.turn_id)
            head.audio.clear()
            head.model_text = ""
            head.output_started = False
            head.ignore_next_complete = not sc.turn_complete
            return
        if sc.model_turn and not head.dropped:
            for part in sc.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    head.audio.extend(part.inline_data.data)
                    head.output_started = True
            limit = FIRST_AUDIO_FLUSH_BYTES if head.audio_chunks_sent == 0 else AUDIO_FLUSH_BYTES
            if len(head.audio) >= limit:
                await self._flush_audio(ws, head)
        elif sc.model_turn:
            head.output_started = True
        if sc.output_transcription and sc.output_transcription.text and not head.dropped:
            head.model_text += sc.output_transcription.text
            if head.audio_chunks_sent:
                await self._send_json(
                    ws,
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": head.model_text.strip(),
                        "final": False,
                        "turn_id": head.turn_id,
                    },
                )
        if (sc.generation_complete or sc.turn_complete) and not head.finalized:
            if head.dropped:
                head.finalized = True
            elif not head.ignore_next_complete:
                await self._finalize_turn(ws, head)
        if sc.turn_complete:
            if head.ignore_next_complete:
                head.ignore_next_complete = False
                return
            self._turns.pop(0)

    async def _flush_audio(self, ws: WebSocket, turn: _LiveTurn) -> None:
        if not turn.audio:
            return
        if not turn.user_sent:
            await self._send_user_transcript(ws, turn)
        chunk = pcm16_to_wav(bytes(turn.audio), OUT_RATE)
        turn.audio.clear()
        if await self._safe_send_bytes(ws, chunk):
            turn.audio_chunks_sent += 1
        self._reset_inactivity()

    async def _send_user_transcript(self, ws: WebSocket, turn: _LiveTurn) -> None:
        turn.user_sent = True
        if turn.is_greeting or not turn.user_text.strip():
            return
        await self._send_json(
            ws,
            {
                "type": "transcript",
                "role": "user",
                "text": turn.user_text.strip(),
                "final": True,
                "turn_id": turn.turn_id,
            },
        )

    async def _finalize_turn(self, ws: WebSocket, turn: _LiveTurn) -> None:
        await self._flush_audio(ws, turn)
        if not turn.user_sent:
            await self._send_user_transcript(ws, turn)
        text = self._extract_speech_text(turn.model_text)
        if text:
            await self._send_json(
                ws,
                {
                    "type": "transcript",
                    "role": "assistant",
                    "text": text,
                    "final": True,
                    "turn_id": turn.turn_id,
                },
            )
            completed_at = datetime.now(UTC).replace(tzinfo=None)
            user_text = turn.user_text.strip()
            if turn.is_greeting:
                self.history.append({"role": "assistant", "content": text})
                self._pending_saves.append(
                    asyncio.create_task(self._save_message("assistant", text))
                )
            elif user_text:
                self.history += [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": text},
                ]
                self._pending_saves.append(
                    asyncio.create_task(self._save_turn(user_text, text, completed_at=completed_at))
                )
            logger.info(
                "[gemini-live] turn %s complete: user=%r assistant=%r latency_ms=%.0f",
                turn.turn_id,
                user_text[:80],
                text[:120],
                (time.monotonic() - turn.sent_at) * 1000,
            )
        await self._finish_turn_ui(ws, turn)

    async def _finish_turn_ui(self, ws: WebSocket, turn: _LiveTurn) -> None:
        turn.finalized = True
        await self._send_status(ws, turn.turn_id, "listening")
        await self._send_json(ws, {"type": "turn_complete", "turn_id": turn.turn_id})
        self._reset_inactivity()

    async def _handle_tool_call(
        self, session: object, ws: WebSocket, tool_call: types.LiveServerToolCall
    ) -> None:
        responses = []
        saved = False
        for fc in tool_call.function_calls or []:
            call = LLMToolCall(
                id=fc.id or "",
                name=fc.name or "",
                arguments=dict(fc.args or {}),
                raw_arguments=json.dumps(fc.args or {}),
            )
            try:
                async with db_session() as db_mem:
                    result = await execute_save_user_memory(
                        db_mem, self._user_id, call, "voice", study_plan_id=self._study_plan_id
                    )
                content = result.content
            except Exception as exc:
                logger.warning("[gemini-live] memory tool failed: %s", self._fmt_exc(exc))
                content = {"saved": False, "error": "internal_error"}
            saved = saved or content.get("saved") is True
            responses.append(
                types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response=content,
                    scheduling=types.FunctionResponseScheduling.SILENT,
                )
            )
        if responses:
            await session.send_tool_response(function_responses=responses)
        if saved:
            head = self._turns[0] if self._turns else None
            await self._send_memory_updated(ws, head.turn_id if head else self._turn_id)

    async def _save_live_usage(self, usage: types.UsageMetadata) -> None:
        if self._user_id is None or not usage.total_token_count:
            return
        try:
            from app.models.llm_usage import LLMUsage  # noqa: PLC0415

            async with db_session() as db:
                db.add(
                    LLMUsage(
                        user_id=self._user_id,
                        source="conversation",
                        prompt_tokens=usage.prompt_token_count,
                        completion_tokens=usage.response_token_count,
                        total_tokens=usage.total_token_count,
                        study_plan_id=self._study_plan_id,
                    )
                )
                await db.commit()
        except Exception:
            logger.debug("[gemini-live] Failed to save token usage — ignored")

    # --- Browser side ---

    async def run(self, ws: WebSocket) -> None:
        logger.info("[gemini-live] Conversation loop starting")
        self._timer_tasks = [
            asyncio.create_task(self._max_duration_watcher(ws)),
            asyncio.create_task(self._inactivity_watcher(ws)),
            asyncio.create_task(self._no_reply_watcher(ws)),
        ]
        self._session_task = asyncio.create_task(self._session_loop(ws))
        browser_task = asyncio.create_task(self._browser_loop(ws))
        try:
            done, _ = await asyncio.wait(
                {self._session_task, browser_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if self._session_task in done and self._session_task.exception() is not None:
                exc = self._session_task.exception()
                logger.error("[gemini-live] session failed: %s", self._fmt_exc(exc))
                reason = record_ai_failure("voice", exc)
                try:
                    await self._send_json(
                        ws,
                        {
                            "type": "error",
                            "code": "internal_error",
                            "message": str(exc),
                            "ai_error": reason,
                        },
                    )
                    await self._close_ws(ws, code=1011)
                except Exception:
                    logger.debug("[gemini-live] could not report session failure")
        finally:
            self._closing = True
            for task in (browser_task, self._session_task, *self._timer_tasks):
                task.cancel()
            await asyncio.gather(browser_task, self._session_task, return_exceptions=True)
            logger.info(
                "[gemini-live] Session close reason=%s",
                self._client_close_reason or "peer_disconnect/unknown",
            )

    async def _browser_loop(self, ws: WebSocket) -> None:
        while True:
            try:
                data = await ws.receive()
            except RuntimeError:
                logger.info("[gemini-live] ws.receive raised RuntimeError — disconnect")
                return
            if data.get("type") == "websocket.disconnect":
                return
            if data.get("bytes"):
                await self.handle_audio(data["bytes"], ws)
            elif data.get("text"):
                msg = json.loads(data["text"])
                if msg.get("type") == "client_event":
                    if msg.get("event") == "session_close_request":
                        self._client_close_reason = msg.get("reason", "manual")
                        return
                    continue
                if msg.get("type") == "interrupt":
                    self._drop_in_flight()
                    await self._send_json(ws, {"type": "interrupted"})

    def _drop_in_flight(self) -> bool:
        """Mark replies still streaming as stale; forget activities Gemini never answered."""
        dropped = False
        kept: list[_LiveTurn] = []
        carried_user_text = ""
        for turn in self._turns:
            if turn.output_started:
                dropped = dropped or not turn.finalized
                turn.dropped = True
                kept.append(turn)
            elif not turn.is_greeting:
                # Gemini merges an unanswered activity into the next one.
                carried_user_text += turn.user_text
        self._turns = kept
        self._carried_user_text = carried_user_text
        return dropped

    async def handle_audio(self, audio_bytes: bytes, ws: WebSocket) -> None:
        self._reset_inactivity()
        try:
            pcm = wav_to_pcm16(audio_bytes)
        except Exception as exc:
            logger.warning("[gemini-live] unreadable utterance: %s", self._fmt_exc(exc))
            return
        if not pcm:
            return
        if self._drop_in_flight():
            await self._send_json(ws, {"type": "barge_in"})
        turn = _LiveTurn(self._next_turn_id(), user_text=self._carried_user_text)
        self._turns.append(turn)
        await self._send_status(ws, turn.turn_id, "transcribing")
        await asyncio.wait_for(self._session_ready.wait(), timeout=30)
        session = self._session
        await session.send_realtime_input(activity_start=types.ActivityStart())
        for i in range(0, len(pcm), SEND_CHUNK_BYTES):
            await session.send_realtime_input(
                audio=types.Blob(
                    data=pcm[i : i + SEND_CHUNK_BYTES], mime_type=f"audio/pcm;rate={IN_RATE}"
                )
            )
        await session.send_realtime_input(activity_end=types.ActivityEnd())
        turn.sent_at = time.monotonic()
        await self._send_status(ws, turn.turn_id, "thinking")

    async def _no_reply_watcher(self, ws: WebSocket) -> None:
        """Release the UI if Gemini never answers an utterance (e.g. it heard only noise)."""
        while True:
            await asyncio.sleep(2)
            now = time.monotonic()
            for turn in list(self._turns):
                if (
                    not turn.output_started
                    and not turn.finalized
                    and now - turn.sent_at > NO_REPLY_TIMEOUT_SECONDS
                ):
                    logger.info("[gemini-live] no reply for turn %s; releasing", turn.turn_id)
                    self._turns.remove(turn)
                    await self._finish_turn_ui(ws, turn)

    async def cancel_current(self) -> None:
        self._drop_in_flight()

    async def cleanup(self) -> None:
        self._closing = True
        if self._session_task is not None and not self._session_task.done():
            self._session_task.cancel()
            await asyncio.gather(self._session_task, return_exceptions=True)
        await super().cleanup()
