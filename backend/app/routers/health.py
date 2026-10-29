from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import get_current_user, require_admin
from app.core.limiter import limiter
from app.models.user import User
from app.services.ai_errors import get_last_ai_failure
from app.utils.redis import redis_client as _redis_client

router = APIRouter(tags=["health"])

# How long a provider failure keeps showing in the app's AI status banner.
AI_STATUS_WINDOW_SECONDS = 15 * 60


@router.get("/health")
@limiter.limit("60/minute")
async def health(request: Request) -> JSONResponse:  # noqa: ARG001
    return JSONResponse({"status": "ok"})


@router.get("/api/ai-status")
@limiter.limit("60/minute")
async def ai_status(
    request: Request,  # noqa: ARG001
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """The most recent AI provider failure, if it happened recently."""
    try:
        failure = await get_last_ai_failure()
    except Exception:
        failure = None
    if not failure or time.time() - failure["at"] > AI_STATUS_WINDOW_SECONDS:
        return JSONResponse({"failure": None})
    if current_user.role != "admin":
        failure = {k: v for k, v in failure.items() if k != "detail"}
    return JSONResponse({"failure": failure})


@router.get("/api/admin/health")
@limiter.limit("60/minute")
async def admin_health(
    request: Request,
    _admin: User = Depends(require_admin),
) -> JSONResponse:
    checks: dict[str, str] = {}
    ok = True

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        ok = False

    try:
        async with _redis_client() as redis:
            await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        ok = False

    try:
        tts = getattr(request.app.state, "tts_service", None)
        if tts is not None:
            await tts.health()
        checks["tts"] = "ok"
    except Exception as exc:
        checks["tts"] = f"error: {exc}"
        ok = False

    try:
        stt = getattr(request.app.state, "stt_service", None)
        if stt is not None:
            await stt.health()
        checks["stt"] = "ok"
    except Exception as exc:
        checks["stt"] = f"error: {exc}"
        ok = False

    try:
        last_ai_failure = await get_last_ai_failure()
    except Exception:
        last_ai_failure = None

    status_code = 200 if ok else 503
    return JSONResponse(
        {
            "status": "ok" if ok else "degraded",
            "checks": checks,
            "last_ai_failure": last_ai_failure,
        },
        status_code=status_code,
    )
