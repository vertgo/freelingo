"""Classify AI provider failures so users see why a feature stopped working.

Every LLM, TTS and STT failure is reduced to a short reason code. The code
travels to the browser in the ``X-AI-Error`` response header (added by
``AIErrorMiddleware``), in SSE and WebSocket error payloads, and the most
recent failure is kept in Redis for the admin health check.
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Literal, ParamSpec, TypeVar

import httpx
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.app_logger import get_logger
from app.services.user_api_keys import current_user_api_key
from app.utils.redis import redis_client

logger = get_logger(__name__)

AIErrorCode = Literal["quota", "auth", "overloaded", "timeout", "unreachable", "unknown"]
AIService = Literal["llm", "tts", "stt", "voice"]

AI_ERROR_HEADER = "X-AI-Error"
AI_SERVICE_HEADER = "X-AI-Service"
LAST_FAILURE_KEY = "ai:last_failure"

_request_failure: ContextVar[dict | None] = ContextVar("ai_request_failure", default=None)
_background_tasks: set[asyncio.Task] = set()
# Services this process has seen fail; a later success clears the stored failure.
_failing_services: set[str] = set()


def _causes(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status(exc: BaseException) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def classify_ai_error(exc: BaseException) -> AIErrorCode:
    """Map a provider exception (or one wrapping it) to a reason code."""
    for cause in _causes(exc):
        status = _status(cause)
        text = f"{getattr(cause, 'status', '')} {cause}".lower()
        if status == 429 or any(
            word in text for word in ("resource_exhausted", "quota", "rate limit")
        ):
            return "quota"
        if (
            status in (401, 403)
            or "api_key_invalid" in text
            or "api key not valid" in text
            or "valid api key" in text
            or "permission_denied" in text
            or "unauthenticated" in text
        ):
            return "auth"
        if isinstance(cause, (TimeoutError, httpx.TimeoutException)) or "timed out" in text:
            return "timeout"
        if "timeout" in type(cause).__name__.lower():
            return "timeout"
        if status in (500, 502, 503, 504) or any(
            word in text for word in ("overloaded", "high demand", "unavailable")
        ):
            return "overloaded"
        if isinstance(cause, (ConnectionError, httpx.NetworkError)) or any(
            word in text for word in ("connection", "unreachable")
        ):
            return "unreachable"
    return "unknown"


def record_ai_failure(
    service: AIService, exc: BaseException | None = None, *, code: AIErrorCode | None = None
) -> AIErrorCode:
    """Classify and remember a failure; returns the reason code."""
    code = code or (classify_ai_error(exc) if exc is not None else "unknown")
    detail = str(exc)[:300] if exc is not None else ""
    logger.warning("ai_provider_failure", service=service, code=code, detail=detail)

    holder = _request_failure.get()
    if holder is not None:
        holder.update(code=code, service=service)

    # A learner's own key failing says nothing about the server's key.
    if current_user_api_key() is not None:
        return code
    _failing_services.add(service)
    _spawn(_persist(service, code, detail))
    return code


def record_ai_success(service: AIService) -> None:
    """Clear a stored failure for ``service`` once it works again."""
    if service not in _failing_services or current_user_api_key() is not None:
        return
    _failing_services.discard(service)
    _spawn(_clear(service))


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


P = ParamSpec("P")
R = TypeVar("R")


def reports_ai_failure(
    service: AIService,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorate a provider call so any exception it raises is recorded."""

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                result = await fn(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                record_ai_failure(service, exc)
                raise
            record_ai_success(service)
            return result

        return wrapper

    return decorate


async def _persist(service: str, code: str, detail: str) -> None:
    payload = {"service": service, "code": code, "detail": detail, "at": int(time.time())}
    try:
        async with redis_client() as redis:
            await redis.set(LAST_FAILURE_KEY, json.dumps(payload), ex=7 * 24 * 3600)
    except Exception:
        logger.warning("ai_failure_persist_failed", exc_info=True)


async def _clear(service: str) -> None:
    try:
        async with redis_client() as redis:
            raw = await redis.get(LAST_FAILURE_KEY)
            if raw and json.loads(raw).get("service") == service:
                await redis.delete(LAST_FAILURE_KEY)
    except Exception:
        logger.warning("ai_failure_clear_failed", exc_info=True)


async def get_last_ai_failure() -> dict | None:
    async with redis_client() as redis:
        raw = await redis.get(LAST_FAILURE_KEY)
    return json.loads(raw) if raw else None


class AIErrorMiddleware:
    """Adds X-AI-Error / X-AI-Service to error responses caused by an AI failure."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        holder: dict = {}
        started = False
        token = _request_failure.set(holder)

        async def send_with_reason(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if message["status"] >= 400 and holder.get("code"):
                    headers = [*message.get("headers", []), *_reason_headers(holder)]
                    message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_reason)
        except Exception:
            # An endpoint let the provider error escape. Answer with the reason
            # instead of a bare 500 so the browser can explain what happened.
            if started or not holder.get("code"):
                raise
            logger.warning("ai_failure_unhandled", path=scope.get("path"), exc_info=True)
            response = JSONResponse(
                {"detail": "The AI provider request failed.", "ai_error": holder["code"]},
                status_code=503,
            )
            response.raw_headers.extend(_reason_headers(holder))
            await response(scope, receive, send)
        finally:
            _request_failure.reset(token)


def _reason_headers(holder: dict) -> list[tuple[bytes, bytes]]:
    return [
        (AI_ERROR_HEADER.lower().encode(), holder["code"].encode()),
        (AI_SERVICE_HEADER.lower().encode(), holder["service"].encode()),
    ]
