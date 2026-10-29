"""Tests for AI provider failure classification and reporting."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import openai
import pytest
from fastapi import FastAPI, HTTPException
from google.genai import errors as genai_errors
from httpx import ASGITransport, AsyncClient

from app.services import ai_errors
from app.services.ai_errors import (
    AIErrorMiddleware,
    classify_ai_error,
    record_ai_failure,
    reports_ai_failure,
)
from app.services.llm_adapter import LLMError, LLMTimeoutError


def _openai_status(cls, status: int, message: str):
    request = httpx.Request("POST", "https://example.test")
    return cls(message, response=httpx.Response(status, request=request), body=None)


def _genai(code: int, status: str, message: str) -> genai_errors.APIError:
    return genai_errors.APIError(
        code, {"error": {"code": code, "status": status, "message": message}}
    )


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_openai_status(openai.RateLimitError, 429, "You exceeded your current quota"), "quota"),
        (_genai(429, "RESOURCE_EXHAUSTED", "Quota exceeded"), "quota"),
        (_openai_status(openai.AuthenticationError, 401, "bad key"), "auth"),
        (_openai_status(openai.BadRequestError, 400, "Please pass a valid API key"), "auth"),
        (
            _genai(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
            "auth",
        ),
        (_genai(403, "PERMISSION_DENIED", "denied"), "auth"),
        (_genai(503, "UNAVAILABLE", "The model is overloaded"), "overloaded"),
        (TimeoutError(), "timeout"),
        (httpx.ConnectError("connection refused"), "unreachable"),
        (ValueError("something odd"), "unknown"),
    ],
)
def test_classify_ai_error(exc, code):
    assert classify_ai_error(exc) == code


def test_classify_follows_wrapped_cause():
    try:
        try:
            raise _genai(429, "RESOURCE_EXHAUSTED", "Quota exceeded")
        except genai_errors.APIError as inner:
            raise LLMError("gemini error") from inner
    except LLMError as outer:
        assert classify_ai_error(outer) == "quota"


def test_classify_llm_timeout_by_type():
    assert classify_ai_error(LLMTimeoutError("deadline")) == "timeout"


@pytest.fixture
def stored():
    """Capture what would be written to Redis."""
    calls: list[tuple] = []

    async def persist(service, code, detail):
        calls.append(("set", service, code))

    async def clear(service):
        calls.append(("clear", service))

    with (
        patch.object(ai_errors, "_persist", persist),
        patch.object(ai_errors, "_clear", clear),
    ):
        ai_errors._failing_services.clear()
        yield calls


@pytest.mark.asyncio
async def test_decorator_records_failure_then_clears_on_success(stored):
    calls = {"n": 0}

    @reports_ai_failure("tts")
    async def synthesize():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _genai(429, "RESOURCE_EXHAUSTED", "Quota exceeded")
        return b"audio"

    with pytest.raises(genai_errors.APIError):
        await synthesize()
    assert await synthesize() == b"audio"
    await _drain()
    assert stored == [("set", "tts", "quota"), ("clear", "tts")]


@pytest.mark.asyncio
async def test_success_without_prior_failure_does_not_touch_redis(stored):
    @reports_ai_failure("stt")
    async def transcribe():
        return "hola"

    await transcribe()
    await _drain()
    assert stored == []


@pytest.mark.asyncio
async def test_middleware_tags_error_responses(stored):
    app = FastAPI()
    app.add_middleware(AIErrorMiddleware)

    @app.get("/fails")
    async def fails():
        record_ai_failure("tts", _genai(429, "RESOURCE_EXHAUSTED", "Quota exceeded"))
        raise HTTPException(status_code=503, detail="TTS unavailable")

    @app.get("/escapes")
    async def escapes():
        record_ai_failure("tts", _genai(400, "INVALID_ARGUMENT", "API key not valid"))
        raise _genai(400, "INVALID_ARGUMENT", "API key not valid")

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        failed = await ac.get("/fails")
        fine = await ac.get("/ok")
        escaped = await ac.get("/escapes")

    assert failed.status_code == 503
    assert failed.headers["x-ai-error"] == "quota"
    assert failed.headers["x-ai-service"] == "tts"
    assert "x-ai-error" not in fine.headers
    assert escaped.status_code == 503
    assert escaped.headers["x-ai-error"] == "auth"
    assert escaped.json()["ai_error"] == "auth"


@pytest.mark.asyncio
async def test_ai_status_reports_recent_failure_without_detail_for_users(client, test_user):
    _, headers = test_user
    failure = {"service": "llm", "code": "quota", "detail": "secret-ish", "at": int(time.time())}
    with patch("app.routers.health.get_last_ai_failure", AsyncMock(return_value=failure)):
        res = await client.get("/api/ai-status", headers=headers)
    assert res.status_code == 200
    assert res.json()["failure"] == {"service": "llm", "code": "quota", "at": failure["at"]}


@pytest.mark.asyncio
async def test_ai_status_shows_detail_to_admins(client, admin_user):
    _, headers = admin_user
    failure = {
        "service": "tts",
        "code": "auth",
        "detail": "API key not valid",
        "at": int(time.time()),
    }
    with patch("app.routers.health.get_last_ai_failure", AsyncMock(return_value=failure)):
        res = await client.get("/api/ai-status", headers=headers)
    assert res.json()["failure"]["detail"] == "API key not valid"


@pytest.mark.asyncio
async def test_ai_status_ignores_old_failures(client, test_user):
    _, headers = test_user
    failure = {"service": "llm", "code": "quota", "detail": "", "at": int(time.time()) - 3600}
    with patch("app.routers.health.get_last_ai_failure", AsyncMock(return_value=failure)):
        res = await client.get("/api/ai-status", headers=headers)
    assert res.json() == {"failure": None}


async def _drain() -> None:
    import asyncio  # noqa: PLC0415

    while ai_errors._background_tasks:
        await asyncio.gather(*ai_errors._background_tasks)
