"""Focus areas: recurring mistakes found by reviewing the learner's conversations and lessons.

After a voice session ends, or once a text chat has enough new messages, the
new part of the transcript is sent to the LLM, which reports mistakes grouped
by concept. When a lesson is completed, its wrong answers are reviewed the same
way. Repeats of the same concept are merged and counted. The learner's
top focus areas go back into the tutor prompt, and they can practise each one
with a drill or turn it into flashcards. A focus area is marked mastered after
MASTERY_STREAK correct uses (in conversation or drills) without the mistake.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from html import escape
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_logger import get_logger
from app.models.chat_history import ChatHistory
from app.models.conversation import Conversation
from app.models.focus_area import FocusArea
from app.models.lesson import Exercise, Lesson
from app.models.study_plan import StudyPlan
from app.models.user import User
from app.services.language_helpers import get_language_name, get_native_language_name
from app.services.llm_adapter import llm_adapter
from app.services.prompts import focus as prompts
from app.services.prompts.common import get_language_prompt_overlay
from app.utils.db import db_session
from app.utils.lesson_exercises import normalized_exercise_text

logger = get_logger(__name__)

# Review a text chat once it has this many unreviewed student messages.
CHAT_REVIEW_EVERY = 6
# Smaller segments aren't worth a review (voice sessions and the catch-up sweep).
MIN_STUDENT_MESSAGES = 2
CONTEXT_MESSAGES = 2
MAX_REVIEW_MESSAGES = 60
MAX_MESSAGE_CHARS = 600
MAX_EXAMPLES = 5
MASTERY_STREAK = 3
PROMPT_FOCUS_AREAS = 3
EXISTING_FOR_REVIEW = 25
SWEEP_CONVERSATIONS = 3
# Lesson exercises scoring below this count as mistakes for review.
LESSON_MISTAKE_SCORE = 0.8
MAX_LESSON_EXERCISES = 12
# Speech-recognition errors aren't language mistakes.
SKIP_LESSON_EXERCISE_TYPES = frozenset({"pronunciation"})
DRILL_LENGTH = 5
DRILL_PASS_RATIO = 0.8

Category = Literal["grammar", "vocabulary", "word_order", "other"]

_in_flight: set[int] = set()
_lessons_in_flight: set[int] = set()
_background_tasks: set[asyncio.Task] = set()


class ReviewedMistake(BaseModel):
    concept: str = Field(max_length=120)
    category: Category = "grammar"
    explanation: str = Field(default="", max_length=400)
    said: str = Field(max_length=300)
    correction: str = Field(max_length=300)
    existing_id: int | None = None


class ConversationReview(BaseModel):
    # Required, so a reply with the wrong top-level keys fails and is retried.
    mistakes: list[ReviewedMistake] = Field(max_length=8)
    correct_uses: list[int] = Field(max_length=25)


class DrillItem(BaseModel):
    sentence: str = Field(max_length=300)
    answer: str = Field(max_length=100)
    accepted: list[str] = Field(default_factory=list, max_length=5)
    hint: str = Field(default="", max_length=200)
    translation: str = Field(default="", max_length=300)


class Drill(BaseModel):
    items: list[DrillItem] = Field(min_length=1, max_length=10)


class FocusFlashcard(BaseModel):
    word: str = Field(max_length=100)
    definition: str = Field(max_length=400)
    example_sentence: str = Field(max_length=300)
    translation: str = Field(max_length=255)


class FocusFlashcards(BaseModel):
    cards: list[FocusFlashcard] = Field(min_length=1, max_length=5)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def concept_key(concept: str) -> str:
    return re.sub(r"\s+", " ", concept.strip().lower())[:120]


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ── Reading focus areas ──────────────────────────────────────────────────────


async def get_focus_areas(
    db: AsyncSession, user_id: int, target_language: str, *, include_inactive: bool = True
) -> list[FocusArea]:
    query = select(FocusArea).where(
        FocusArea.user_id == user_id, FocusArea.target_language == target_language
    )
    if not include_inactive:
        query = query.where(FocusArea.status == "active")
    query = query.order_by(FocusArea.occurrences.desc(), FocusArea.last_seen_at.desc())
    return list((await db.execute(query)).scalars().all())


async def get_prompt_focus_areas(
    db: AsyncSession, user_id: int, target_language: str
) -> list[FocusArea]:
    areas = await get_focus_areas(db, user_id, target_language, include_inactive=False)
    return areas[:PROMPT_FOCUS_AREAS]


def build_focus_context(areas: list[FocusArea]) -> str:
    """Format the top focus areas for the tutor system prompt ("" when none)."""
    if not areas:
        return ""
    lines = []
    for area in areas:
        example = area.examples[0] if area.examples else None
        text = f"{area.concept}: {area.explanation}"
        if example:
            text += f' Example: "{example["said"]}" → "{example["correction"]}"'
        lines.append(f"<focus_area>{escape(text, quote=False)}</focus_area>")
    return (
        f"{prompts.FOCUS_CONTEXT_HEADER}\n<focus_areas>\n" + "\n".join(lines) + "\n</focus_areas>\n"
    )


async def load_focus_context(user_id: int, target_language: str) -> str:
    """Best-effort focus context for a tutor prompt."""
    try:
        async with db_session() as db:
            return build_focus_context(await get_prompt_focus_areas(db, user_id, target_language))
    except Exception:
        logger.exception("focus_context_failed", user_id=user_id)
        return ""


# ── Reviewing conversations ──────────────────────────────────────────────────


async def _student_messages_after(db: AsyncSession, conversation_id: int, after_id: int) -> int:
    return (
        await db.execute(
            select(func.count(ChatHistory.id)).where(
                ChatHistory.conversation_id == conversation_id,
                ChatHistory.role == "user",
                ChatHistory.id > after_id,
            )
        )
    ).scalar_one()


def schedule_review(conversation_id: int, *, min_student_messages: int = MIN_STUDENT_MESSAGES):
    """Review the conversation's new messages in the background, if there are enough."""
    if conversation_id in _in_flight:
        return
    _spawn(review_conversation(conversation_id, min_student_messages=min_student_messages))


def schedule_sweep(user_id: int, target_language: str) -> None:
    """Catch up on recent conversations that ended before reaching a review."""
    _spawn(_sweep(user_id, target_language))


async def _sweep(user_id: int, target_language: str) -> None:
    try:
        async with db_session() as db:
            ids = (
                (
                    await db.execute(
                        select(Conversation.id)
                        .where(
                            Conversation.user_id == user_id,
                            Conversation.target_language == target_language,
                        )
                        .order_by(Conversation.updated_at.desc())
                        .limit(SWEEP_CONVERSATIONS)
                    )
                )
                .scalars()
                .all()
            )
        for conversation_id in ids:
            if conversation_id not in _in_flight:
                await review_conversation(conversation_id)
    except Exception:
        logger.exception("focus_sweep_failed", user_id=user_id)


async def review_conversation(
    conversation_id: int, *, min_student_messages: int = MIN_STUDENT_MESSAGES
) -> int:
    """Review unreviewed messages of one conversation; returns mistakes recorded."""
    if conversation_id in _in_flight:
        return 0
    _in_flight.add(conversation_id)
    try:
        return await _review(conversation_id, min_student_messages)
    except Exception:
        logger.exception("focus_review_failed", conversation_id=conversation_id)
        return 0
    finally:
        _in_flight.discard(conversation_id)


async def _review(conversation_id: int, min_student_messages: int) -> int:
    async with db_session() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None or not conv.target_language:
            return 0
        after_id = conv.focus_reviewed_through_id or 0
        if await _student_messages_after(db, conversation_id, after_id) < min_student_messages:
            return 0
        user = await db.get(User, conv.user_id)
        if user is None:
            return 0
        new = list(
            (
                await db.execute(
                    select(ChatHistory)
                    .where(
                        ChatHistory.conversation_id == conversation_id, ChatHistory.id > after_id
                    )
                    .order_by(ChatHistory.id.desc())
                    .limit(MAX_REVIEW_MESSAGES)
                )
            )
            .scalars()
            .all()
        )[::-1]
        earlier = list(
            (
                await db.execute(
                    select(ChatHistory)
                    .where(
                        ChatHistory.conversation_id == conversation_id, ChatHistory.id <= after_id
                    )
                    .order_by(ChatHistory.id.desc())
                    .limit(CONTEXT_MESSAGES)
                )
            )
            .scalars()
            .all()
        )[::-1]
        cefr_level = "B1"
        if conv.study_plan_id:
            plan = await db.get(StudyPlan, conv.study_plan_id)
            if plan is not None:
                cefr_level = plan.cefr_level
        existing = (await get_focus_areas(db, user.id, conv.target_language))[:EXISTING_FOR_REVIEW]
        existing_text = (
            "\n".join(f"{a.id}: {a.concept} — {a.explanation}" for a in existing) or "(none yet)"
        )
        user_id, target_language, native_language = (
            user.id,
            conv.target_language,
            user.native_language,
        )
        is_voice = conv.source == "voice" or any(m.modality == "voice" for m in new)
        through_id = new[-1].id

    system = prompts.REVIEW_SYSTEM_PROMPT.format(
        target_language_name=get_language_name(target_language),
        cefr_level=cefr_level,
        native_language_name=get_native_language_name(native_language),
        language_prompt_overlay=get_language_prompt_overlay(target_language),
        voice_rule=prompts.VOICE_RULE if is_voice else "",
        existing=existing_text,
    )
    transcript = (
        "<earlier_context>\n"
        + _format_messages(earlier)
        + "\n</earlier_context>\n<new_messages>\n"
        + _format_messages(new)
        + "\n</new_messages>"
    )
    review = await llm_adapter.structured_output(
        [{"role": "system", "content": system}, {"role": "user", "content": transcript}],
        ConversationReview,
    )

    async with db_session() as db:
        recorded = await apply_review(db, user_id, target_language, review)
        conv = await db.get(Conversation, conversation_id)
        if conv is not None and (conv.focus_reviewed_through_id or 0) < through_id:
            conv.focus_reviewed_through_id = through_id
        await db.commit()
    logger.info(
        "focus_review_done",
        conversation_id=conversation_id,
        mistakes=recorded,
        correct_uses=len(review.correct_uses),
    )
    return recorded


def schedule_lesson_review(lesson_id: int) -> None:
    """Review a just-completed lesson's wrong answers in the background."""
    if lesson_id in _lessons_in_flight:
        return
    _spawn(review_lesson(lesson_id))


async def review_lesson(lesson_id: int) -> int:
    """Merge mistakes from a lesson's wrong answers into focus areas; returns mistakes recorded.

    Correct answers never count toward mastery: recognising a form among options is weaker
    evidence than producing it in conversation or a drill.
    """
    if lesson_id in _lessons_in_flight:
        return 0
    _lessons_in_flight.add(lesson_id)
    try:
        return await _review_lesson(lesson_id)
    except Exception:
        logger.exception("focus_lesson_review_failed", lesson_id=lesson_id)
        return 0
    finally:
        _lessons_in_flight.discard(lesson_id)


async def _review_lesson(lesson_id: int) -> int:
    async with db_session() as db:
        lesson = await db.get(Lesson, lesson_id)
        if lesson is None:
            return 0
        plan = await db.get(StudyPlan, lesson.study_plan_id)
        user = await db.get(User, plan.user_id) if plan is not None else None
        if plan is None or user is None:
            return 0
        exercises = (
            (
                await db.execute(
                    select(Exercise)
                    .where(
                        Exercise.lesson_id == lesson_id,
                        Exercise.user_answer.is_not(None),
                        Exercise.score < LESSON_MISTAKE_SCORE,
                        Exercise.exercise_type.not_in(SKIP_LESSON_EXERCISE_TYPES),
                    )
                    .order_by(Exercise.id)
                    .limit(MAX_LESSON_EXERCISES)
                )
            )
            .scalars()
            .all()
        )
        exercises = [e for e in exercises if (e.user_answer or "").strip()]
        if not exercises:
            return 0
        existing = (await get_focus_areas(db, user.id, plan.target_language))[:EXISTING_FOR_REVIEW]
        existing_text = (
            "\n".join(f"{a.id}: {a.concept} — {a.explanation}" for a in existing) or "(none yet)"
        )
        grammar_points = ", ".join(
            slug for slug in (lesson.content or {}).get("grammar_refs", []) if isinstance(slug, str)
        )
        user_id, target_language, native_language = (
            user.id,
            plan.target_language,
            user.native_language,
        )
        system = prompts.LESSON_REVIEW_SYSTEM_PROMPT.format(
            target_language_name=get_language_name(target_language),
            cefr_level=plan.cefr_level,
            native_language_name=get_native_language_name(native_language),
            lesson_title=escape(lesson.title, quote=False),
            grammar_points=grammar_points or "(not specified)",
            language_prompt_overlay=get_language_prompt_overlay(target_language),
            existing=existing_text,
        )
        items = "\n".join(_format_exercise(e) for e in exercises)

    review = await llm_adapter.structured_output(
        [{"role": "system", "content": system}, {"role": "user", "content": items}],
        ConversationReview,
    )
    review.correct_uses = []

    async with db_session() as db:
        recorded = await apply_review(db, user_id, target_language, review)
        await db.commit()
    logger.info("focus_lesson_review_done", lesson_id=lesson_id, mistakes=recorded)
    return recorded


def _format_exercise(exercise: Exercise) -> str:
    question, _ = normalized_exercise_text(exercise)
    fields = [
        ("type", exercise.exercise_type),
        ("question", question),
        ("correct_answer", exercise.correct_answer),
        ("student_answer", exercise.user_answer),
        ("feedback", exercise.feedback),
        ("corrections", json.dumps(exercise.corrections, ensure_ascii=False)),
    ]
    body = "\n".join(
        f"{name}: {escape(str(value)[:MAX_MESSAGE_CHARS], quote=False)}"
        for name, value in fields
        if value and value not in ("null", "[]")
    )
    return f"<exercise>\n{body}\n</exercise>"


def _format_messages(messages: list[ChatHistory]) -> str:
    return "\n".join(
        f"{'Student' if m.role == 'user' else 'Tutor'}: {escape(m.content[:MAX_MESSAGE_CHARS])}"
        for m in messages
        if m.content and m.content.strip()
    )


async def apply_review(
    db: AsyncSession, user_id: int, target_language: str, review: ConversationReview
) -> int:
    """Merge one review into the learner's focus areas (caller commits)."""
    areas = await get_focus_areas(db, user_id, target_language)
    by_id = {a.id: a for a in areas}
    by_key = {a.concept_key: a for a in areas}
    now = _now()
    touched: set[int] = set()
    recorded = 0

    for mistake in review.mistakes:
        said, correction = mistake.said.strip(), mistake.correction.strip()
        concept = mistake.concept.strip()
        if not concept or not said or not correction or said == correction:
            continue
        area = by_id.get(mistake.existing_id) if mistake.existing_id else None
        area = area or by_key.get(concept_key(concept))
        example = {"said": said, "correction": correction}
        if area is None:
            area = FocusArea(
                user_id=user_id,
                target_language=target_language,
                concept=concept,
                concept_key=concept_key(concept),
                category=mistake.category,
                explanation=mistake.explanation.strip(),
                examples=[example],
                occurrences=1,
                correct_streak=0,
                status="active",
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(area)
            await db.flush()
            by_key[area.concept_key] = area
            touched.add(area.id)
            recorded += 1
            continue
        if area.id in touched:
            continue
        touched.add(area.id)
        area.occurrences += 1
        area.last_seen_at = now
        area.correct_streak = 0
        others = [e for e in area.examples or [] if e.get("said") != said]
        area.examples = [example, *others][:MAX_EXAMPLES]
        if not area.explanation and mistake.explanation:
            area.explanation = mistake.explanation.strip()
        if area.status == "mastered":
            area.status = "active"
            area.mastered_at = None
        recorded += 1

    for area_id in set(review.correct_uses):
        area = by_id.get(area_id)
        if area is not None and area.id not in touched:
            record_correct_use(area, now)
    return recorded


def record_correct_use(area: FocusArea, now: datetime | None = None) -> None:
    area.correct_streak += 1
    if area.status == "active" and area.correct_streak >= MASTERY_STREAK:
        area.status = "mastered"
        area.mastered_at = now or _now()


# ── Practice ─────────────────────────────────────────────────────────────────


def _examples_text(area: FocusArea) -> str:
    return (
        "\n".join(f'- "{e["said"]}" → "{e["correction"]}"' for e in area.examples or []) or "(none)"
    )


def _practice_prompt(
    template: str, area: FocusArea, native_language: str, cefr_level: str, count: int
):
    return template.format(
        target_language_name=get_language_name(area.target_language),
        cefr_level=cefr_level,
        native_language_name=get_native_language_name(native_language),
        language_prompt_overlay=get_language_prompt_overlay(area.target_language),
        concept=area.concept,
        explanation=area.explanation,
        examples=_examples_text(area),
        count=count,
    )


async def generate_drill(area: FocusArea, native_language: str, cefr_level: str) -> Drill:
    system = _practice_prompt(
        prompts.DRILL_SYSTEM_PROMPT, area, native_language, cefr_level, DRILL_LENGTH
    )
    drill = await llm_adapter.structured_output(
        [{"role": "system", "content": system}, {"role": "user", "content": "Write the drill."}],
        Drill,
    )
    drill.items = [i for i in drill.items if "___" in i.sentence and i.answer.strip()]
    if not drill.items:
        raise ValueError("Drill had no usable items")
    return drill


async def generate_flashcards(
    area: FocusArea, native_language: str, cefr_level: str
) -> list[FocusFlashcard]:
    system = _practice_prompt(
        prompts.FLASHCARDS_SYSTEM_PROMPT, area, native_language, cefr_level, 3
    )
    result = await llm_adapter.structured_output(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "Make the flashcards."},
        ],
        FocusFlashcards,
    )
    return result.cards
