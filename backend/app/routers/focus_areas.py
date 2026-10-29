from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_logger import get_logger
from app.core.database import get_db
from app.core.deps import get_active_study_plan, get_current_user, require_not_maintenance
from app.core.limiter import limiter
from app.models.flashcard import Flashcard
from app.models.focus_area import FocusArea
from app.models.study_plan import StudyPlan
from app.models.user import User
from app.schemas.focus import (
    DrillResponse,
    FocusAreaResponse,
    FocusAreaUpdate,
    FocusFlashcardsResponse,
    PracticeResult,
)
from app.services import focus_service
from app.services.llm_adapter import LLMError, LLMTimeoutError, LLMUnavailableError
from app.services.user_language_service import get_active_language

logger = get_logger(__name__)

router = APIRouter(prefix="/api/focus-areas", tags=["focus-areas"])

FLASHCARD_SOURCE = "focus"


def _response(area: FocusArea) -> FocusAreaResponse:
    return FocusAreaResponse.model_validate(
        {
            **{c: getattr(area, c) for c in FocusAreaResponse.model_fields if hasattr(area, c)},
            "examples": area.examples or [],
            "mastery_streak": focus_service.MASTERY_STREAK,
        }
    )


async def _owned_area(db: AsyncSession, user: User, area_id: int) -> FocusArea:
    area = await db.get(FocusArea, area_id)
    if area is None or area.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Focus area not found")
    return area


def _ai_error(exc: LLMError) -> HTTPException:
    if isinstance(exc, LLMTimeoutError):
        return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "The AI model took too long.")
    if isinstance(exc, LLMUnavailableError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ai_service_unavailable")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "ai_service_error")


@router.get("", response_model=list[FocusAreaResponse])
@limiter.limit("60/minute")
async def list_focus_areas(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Focus areas for the active language, most frequent first.

    Also reviews recent conversations that ended before they were reviewed, in
    the background, so the list catches up on the next visit.
    """
    active = await get_active_language(db, current_user.id)
    if not active:
        return []
    focus_service.schedule_sweep(current_user.id, active.target_language)
    areas = await focus_service.get_focus_areas(db, current_user.id, active.target_language)
    return [_response(a) for a in areas if a.status != "dismissed"]


@router.patch("/{area_id}", response_model=FocusAreaResponse)
@limiter.limit("60/minute")
async def update_focus_area(
    request: Request,
    area_id: int,
    data: FocusAreaUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    area = await _owned_area(db, current_user, area_id)
    area.status = data.status
    if data.status == "mastered":
        area.mastered_at = area.mastered_at or focus_service._now()
    else:
        area.mastered_at = None
        area.correct_streak = 0
    await db.commit()
    return _response(area)


@router.delete("/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_focus_area(
    request: Request,
    area_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    area = await _owned_area(db, current_user, area_id)
    await db.delete(area)
    await db.commit()


@router.post("/{area_id}/drill", response_model=DrillResponse)
@limiter.limit("20/minute")
async def create_drill(
    request: Request,
    area_id: int,
    _maintenance: None = Depends(require_not_maintenance),
    current_user: User = Depends(get_current_user),
    plan: StudyPlan = Depends(get_active_study_plan),
    db: AsyncSession = Depends(get_db),
):
    area = await _owned_area(db, current_user, area_id)
    try:
        drill = await focus_service.generate_drill(
            area, current_user.native_language, plan.cefr_level
        )
    except LLMError as exc:
        raise _ai_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "ai_service_error") from exc
    return DrillResponse.model_validate(drill.model_dump())


@router.post("/{area_id}/practice", response_model=FocusAreaResponse)
@limiter.limit("30/minute")
async def record_practice(
    request: Request,
    area_id: int,
    data: PracticeResult,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A passed drill counts as one correct use toward mastering the focus area."""
    area = await _owned_area(db, current_user, area_id)
    if data.correct <= data.total and data.correct / data.total >= focus_service.DRILL_PASS_RATIO:
        focus_service.record_correct_use(area)
        await db.commit()
    return _response(area)


@router.post("/{area_id}/flashcards", response_model=FocusFlashcardsResponse)
@limiter.limit("20/minute")
async def create_focus_flashcards(
    request: Request,
    area_id: int,
    _maintenance: None = Depends(require_not_maintenance),
    current_user: User = Depends(get_current_user),
    plan: StudyPlan = Depends(get_active_study_plan),
    db: AsyncSession = Depends(get_db),
):
    area = await _owned_area(db, current_user, area_id)
    if area.target_language != plan.target_language:
        raise HTTPException(status.HTTP_409_CONFLICT, "study_context_changed")
    try:
        cards = await focus_service.generate_flashcards(
            area, current_user.native_language, plan.cefr_level
        )
    except LLMError as exc:
        raise _ai_error(exc) from exc
    existing = await db.execute(
        select(Flashcard.word).where(
            Flashcard.user_id == current_user.id, Flashcard.study_plan_id == plan.id
        )
    )
    seen = {focus_service.concept_key(w) for w in existing.scalars().all()}
    created = 0
    for card in cards:
        key = focus_service.concept_key(card.word)
        if not key or key in seen:
            continue
        seen.add(key)
        db.add(
            Flashcard(
                user_id=current_user.id,
                study_plan_id=plan.id,
                word=card.word.strip(),
                definition=card.definition,
                example_sentence=card.example_sentence,
                translation=card.translation,
                source=FLASHCARD_SOURCE,
            )
        )
        created += 1
    await db.commit()
    return FocusFlashcardsResponse(created=created)
