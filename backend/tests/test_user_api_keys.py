"""Tests for learner-supplied Gemini API keys."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from google.genai import errors as genai_errors
from sqlalchemy import select

from app.core.config import settings
from app.models.user import User
from app.services import ai_errors, user_api_keys
from app.services.llm_adapter import LLMAdapter
from app.services.tts_service import GeminiTTSService
from app.services.user_api_keys import (
    current_user_api_key,
    decrypt_api_key,
    encrypt_api_key,
)

KEY = "AIzaSyTestKey-1234567890abcd"


def _genai(code: int, status: str, message: str) -> genai_errors.APIError:
    return genai_errors.APIError(
        code, {"error": {"code": code, "status": status, "message": message}}
    )


@pytest.fixture
def gemini(monkeypatch):
    """Run every AI service on Gemini, as a learner's key would be used."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "TTS_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "STT_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "gemini_live")
    monkeypatch.setattr(settings, "ALLOW_USER_API_KEYS", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "server-key")
    token = user_api_keys._current_key.set(None)
    yield
    user_api_keys._current_key.reset(token)


def test_encrypt_round_trip_and_unreadable_tokens():
    token = encrypt_api_key(KEY)
    assert KEY not in token
    assert decrypt_api_key(token) == KEY
    assert decrypt_api_key("not-a-token") is None
    assert decrypt_api_key(None) is None


@pytest.mark.asyncio
async def test_save_key_stores_it_encrypted(client, test_user, db_session, gemini):
    user, headers = test_user
    with patch("app.routers.auth.validate_gemini_key", AsyncMock()) as validate:
        res = await client.put(
            "/api/auth/me/api-key", json={"api_key": f"  {KEY}\n"}, headers=headers
        )
    assert res.status_code == 200
    body = res.json()
    assert body["has_key"] is True
    assert body["hint"] == "…abcd"
    assert body["services"] == ["llm", "tts", "stt", "voice"]
    validate.assert_awaited_once_with(KEY)

    stored = (
        await db_session.execute(select(User.encrypted_api_key).where(User.id == user.id))
    ).scalar_one()
    assert stored and KEY not in stored
    assert decrypt_api_key(stored) == KEY

    status = await client.get("/api/auth/me/api-key", headers=headers)
    assert status.json()["has_key"] is True


@pytest.mark.asyncio
async def test_rejected_key_is_not_saved(client, test_user, gemini):
    _, headers = test_user
    bad = AsyncMock(side_effect=_genai(400, "INVALID_ARGUMENT", "API key not valid."))
    with patch("app.routers.auth.validate_gemini_key", bad):
        res = await client.put("/api/auth/me/api-key", json={"api_key": KEY}, headers=headers)
    assert res.status_code == 400
    assert res.json()["reason"] == "auth"
    assert "x-ai-error" not in res.headers

    status = await client.get("/api/auth/me/api-key", headers=headers)
    assert status.json()["has_key"] is False


@pytest.mark.asyncio
async def test_key_out_of_quota_is_saved_with_warning(client, test_user, gemini):
    _, headers = test_user
    quota = AsyncMock(side_effect=_genai(429, "RESOURCE_EXHAUSTED", "Quota exceeded"))
    with patch("app.routers.auth.validate_gemini_key", quota):
        res = await client.put("/api/auth/me/api-key", json={"api_key": KEY}, headers=headers)
    assert res.status_code == 200
    assert res.json()["has_key"] is True
    assert res.json()["warning"] == "quota"


@pytest.mark.asyncio
async def test_remove_key(client, test_user, gemini):
    _, headers = test_user
    with patch("app.routers.auth.validate_gemini_key", AsyncMock()):
        await client.put("/api/auth/me/api-key", json={"api_key": KEY}, headers=headers)
    res = await client.delete("/api/auth/me/api-key", headers=headers)
    assert res.status_code == 200
    assert res.json()["has_key"] is False


@pytest.mark.asyncio
async def test_disabled_without_gemini_services(client, test_user, monkeypatch):
    _, headers = test_user
    for name, value in (
        ("LLM_PROVIDER", "openai"),
        ("TTS_PROVIDER", "local"),
        ("STT_PROVIDER", "local"),
        ("VOICE_PROVIDER", "pipeline"),
    ):
        monkeypatch.setattr(settings, name, value)
    status = await client.get("/api/auth/me/api-key", headers=headers)
    assert status.json()["enabled"] is False
    res = await client.put("/api/auth/me/api-key", json={"api_key": KEY}, headers=headers)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_saved_key_reaches_the_endpoint_and_hides_server_failures(
    client, test_user, db_session, gemini
):
    """get_current_user activates the key for the endpoint it guards."""
    user, headers = test_user
    user.encrypted_api_key = encrypt_api_key(KEY)
    await db_session.commit()
    failure = {"service": "tts", "code": "quota", "detail": "", "at": int(time.time())}
    with patch("app.routers.health.get_last_ai_failure", AsyncMock(return_value=failure)):
        res = await client.get("/api/ai-status", headers=headers)
    assert res.json() == {"failure": None, "own_key_services": ["llm", "tts", "stt", "voice"]}


@pytest.mark.asyncio
async def test_learner_without_key_still_sees_server_failures(client, test_user, gemini):
    _, headers = test_user
    failure = {"service": "tts", "code": "quota", "detail": "", "at": int(time.time())}
    with patch("app.routers.health.get_last_ai_failure", AsyncMock(return_value=failure)):
        res = await client.get("/api/ai-status", headers=headers)
    assert res.json()["failure"]["code"] == "quota"
    assert res.json()["own_key_services"] == []


def test_clients_follow_the_current_key(gemini):
    adapter = LLMAdapter()
    tts = GeminiTTSService(api_key="server-key", model="m", voice="Kore")
    assert adapter._chat_client() is adapter.client
    assert tts._client is tts._server_client

    user_api_keys._current_key.set(KEY)
    own_llm = adapter._chat_client()
    own_tts = tts._client
    assert own_llm is not adapter.client
    assert own_llm.api_key == KEY
    assert own_tts is not tts._server_client
    # The same key reuses its client.
    assert adapter._chat_client() is own_llm
    assert tts._client is own_tts
    assert user_api_keys.gemini_api_key() == KEY
    assert current_user_api_key() == KEY


def test_own_key_failures_stay_out_of_the_shared_status(gemini):
    persisted: list = []
    with patch.object(ai_errors, "_persist", lambda *a: persisted.append(a) or _done()):
        user_api_keys._current_key.set(KEY)
        assert (
            ai_errors.record_ai_failure("llm", _genai(400, "INVALID_ARGUMENT", "API key not valid"))
            == "auth"
        )
        user_api_keys._current_key.set(None)
        ai_errors.record_ai_failure("llm", _genai(400, "INVALID_ARGUMENT", "API key not valid"))
    assert len(persisted) == 1


async def _done() -> None:
    return None
