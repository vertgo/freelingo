"""Learner-supplied Gemini API keys.

A learner can save their own Gemini key in Settings. It is stored encrypted
(Fernet, keyed from ``SECRET_KEY``) and, while one of their requests runs, it
replaces the server's ``GEMINI_API_KEY`` for every Gemini-backed service: the
LLM, TTS, STT and Gemini Live voice. If their key fails they see that error;
the server key is never used as a silent fallback.

The key for the current request lives in a ContextVar, set by
``get_current_user`` (HTTP) or the conversation WebSocket. Tasks started from
a request copy the context, so background work it starts uses the same key.
"""

from __future__ import annotations

import base64
import hashlib
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar

from cryptography.fernet import Fernet, InvalidToken

from app.core.app_logger import get_logger
from app.core.config import settings

logger = get_logger(__name__)

_current_key: ContextVar[str | None] = ContextVar("user_api_key", default=None)

_CLIENT_CACHE_SIZE = 64
_clients: OrderedDict[tuple[str, str], object] = OrderedDict()


def _fernet() -> Fernet:
    digest = hashlib.sha256(b"freelingo-user-api-key:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(token: str | None) -> str | None:
    """Return the plaintext key, or None if absent or unreadable (e.g. SECRET_KEY changed)."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.warning("user_api_key_unreadable")
        return None


def key_hint(api_key: str) -> str:
    return f"…{api_key[-4:]}"


def gemini_services() -> list[str]:
    """Services that run on Gemini here, and so would use a learner's key."""
    services = []
    if settings.LLM_PROVIDER == "gemini":
        services.append("llm")
    if settings.TTS_PROVIDER == "gemini":
        services.append("tts")
    if settings.STT_PROVIDER == "gemini":
        services.append("stt")
    if settings.VOICE_PROVIDER == "gemini_live":
        services.append("voice")
    return services


def user_api_keys_enabled() -> bool:
    return settings.ALLOW_USER_API_KEYS and bool(gemini_services())


def use_user_api_key(encrypted: str | None) -> None:
    """Make the given user's key (or none) the key for the current request."""
    _current_key.set(decrypt_api_key(encrypted) if user_api_keys_enabled() else None)


def current_user_api_key() -> str | None:
    return _current_key.get()


def gemini_api_key() -> str:
    """The Gemini key to use right now: the learner's own, else the server's."""
    return _current_key.get() or settings.GEMINI_API_KEY


def client_for_key[T](kind: str, api_key: str, factory: Callable[[str], T]) -> T:
    """Reuse one SDK client per (kind, key), keeping the most recently used few."""
    cache_key = (kind, hashlib.sha256(api_key.encode()).hexdigest())
    client = _clients.get(cache_key)
    if client is None:
        client = factory(api_key)
        _clients[cache_key] = client
        if len(_clients) > _CLIENT_CACHE_SIZE:
            _clients.popitem(last=False)
    else:
        _clients.move_to_end(cache_key)
    return client  # type: ignore[return-value]


async def validate_gemini_key(api_key: str) -> None:
    """Raise the provider's error if the key can't reach the configured model."""
    from google import genai  # noqa: PLC0415

    client = genai.Client(api_key=api_key)
    model = settings.GEMINI_MODEL if settings.LLM_PROVIDER == "gemini" else None
    if model:
        await client.aio.models.get(model=model)
    else:
        pager = await client.aio.models.list(config={"page_size": 1})
        async for _ in pager:
            break
