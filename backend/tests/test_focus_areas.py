"""Tests for focus areas: recurring mistakes found in conversations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.chat_history import ChatHistory
from app.models.conversation import Conversation
from app.models.flashcard import Flashcard
from app.models.focus_area import FocusArea
from app.services import focus_service
from app.services.focus_service import (
    ConversationReview,
    Drill,
    DrillItem,
    FocusFlashcard,
    ReviewedMistake,
    apply_review,
    build_focus_context,
)
from tests.conftest import make_study_plan

LANG = "es-ES"


def _mistake(concept="Ser vs. estar", said="Soy cansado", correction="Estoy cansado", **kw):
    return ReviewedMistake(
        concept=concept,
        explanation="Estar for temporary states.",
        said=said,
        correction=correction,
        **kw,
    )


def _review(mistakes=(), correct_uses=()):
    return ConversationReview(mistakes=list(mistakes), correct_uses=list(correct_uses))


@pytest.fixture
def use_test_db(db_session):
    @asynccontextmanager
    async def session():
        yield db_session

    with patch.object(focus_service, "db_session", session):
        yield


async def _areas(db_session, user_id):
    return list(
        (await db_session.execute(select(FocusArea).where(FocusArea.user_id == user_id)))
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_repeats_merge_into_one_focus_area(db_session, test_user):
    user, _ = test_user
    await apply_review(db_session, user.id, LANG, _review(mistakes=[_mistake()]))
    await db_session.commit()
    [area] = await _areas(db_session, user.id)

    # Same concept by id, and again by label with different casing.
    await apply_review(
        db_session,
        user.id,
        LANG,
        _review(mistakes=[_mistake(said="Es aburrido hoy", existing_id=area.id)]),
    )
    await apply_review(
        db_session,
        user.id,
        LANG,
        _review(mistakes=[_mistake(concept=" ser VS. estar ")]),
    )
    await db_session.commit()
    [area] = await _areas(db_session, user.id)
    assert area.occurrences == 3
    assert [e["said"] for e in area.examples] == ["Soy cansado", "Es aburrido hoy"]


@pytest.mark.asyncio
async def test_same_concept_twice_in_one_review_counts_once(db_session, test_user):
    user, _ = test_user
    review = _review(mistakes=[_mistake(), _mistake(said="Soy enfermo")])
    await apply_review(db_session, user.id, LANG, review)
    await db_session.commit()
    [area] = await _areas(db_session, user.id)
    assert area.occurrences == 1


@pytest.mark.asyncio
async def test_correct_uses_master_and_a_relapse_reactivates(db_session, test_user):
    user, _ = test_user
    await apply_review(db_session, user.id, LANG, _review(mistakes=[_mistake()]))
    await db_session.commit()
    [area] = await _areas(db_session, user.id)
    for _ in range(focus_service.MASTERY_STREAK):
        await apply_review(db_session, user.id, LANG, _review(correct_uses=[area.id]))
    await db_session.commit()
    await db_session.refresh(area)
    assert area.status == "mastered"
    assert area.mastered_at is not None

    await apply_review(db_session, user.id, LANG, _review(mistakes=[_mistake(existing_id=area.id)]))
    await db_session.commit()
    await db_session.refresh(area)
    assert area.status == "active"
    assert area.correct_streak == 0


@pytest.mark.asyncio
async def test_ignores_non_mistakes_and_other_users_ids(db_session, test_user, admin_user):
    user, _ = test_user
    other, _ = admin_user
    await apply_review(db_session, other.id, LANG, _review(mistakes=[_mistake()]))
    await db_session.commit()
    [theirs] = await _areas(db_session, other.id)

    review = _review(
        mistakes=[_mistake(said="Estoy cansado", correction="Estoy cansado")],
        correct_uses=[theirs.id],
    )
    assert await apply_review(db_session, user.id, LANG, review) == 0
    await db_session.commit()
    assert await _areas(db_session, user.id) == []
    await db_session.refresh(theirs)
    assert theirs.correct_streak == 0


def test_focus_context_escapes_and_lists_examples():
    area = FocusArea(
        concept="Ser vs. estar",
        explanation="Use <estar> for states.",
        examples=[{"said": "Soy cansado", "correction": "Estoy cansado"}],
    )
    text = build_focus_context([area])
    assert "&lt;estar&gt;" in text
    assert '"Soy cansado" → "Estoy cansado"' in text
    assert build_focus_context([]) == ""


async def _conversation(db_session, user_id, messages, *, source="chat"):
    conv = Conversation(user_id=user_id, title="t", source=source, target_language=LANG)
    db_session.add(conv)
    await db_session.flush()
    for role, content in messages:
        db_session.add(
            ChatHistory(
                user_id=user_id,
                conversation_id=conv.id,
                role=role,
                content=content,
                target_language=LANG,
                modality="voice" if source == "voice" else "chat",
            )
        )
    await db_session.commit()
    return conv


@pytest.mark.asyncio
async def test_review_conversation_records_and_only_reviews_new_messages(
    db_session, test_user, use_test_db
):
    user, _ = test_user
    conv = await _conversation(
        db_session,
        user.id,
        [
            ("assistant", "¿Cómo estás?"),
            ("user", "Soy cansado"),
            ("assistant", "¿Por qué?"),
            ("user", "Porque yo trabajé mucho ayer"),
        ],
        source="voice",
    )
    structured = AsyncMock(return_value=_review(mistakes=[_mistake()]))
    with patch.object(focus_service.llm_adapter, "structured_output", structured):
        assert await focus_service.review_conversation(conv.id) == 1
        # Nothing new since the last review.
        assert await focus_service.review_conversation(conv.id) == 0
    structured.assert_awaited_once()
    system, transcript = (m["content"] for m in structured.await_args.args[0])
    assert "speech recognition" in system
    assert "<new_messages>" in transcript and "Student: Soy cansado" in transcript
    await db_session.refresh(conv)
    assert conv.focus_reviewed_through_id is not None
    [area] = await _areas(db_session, user.id)
    assert area.concept == "Ser vs. estar"


@pytest.mark.asyncio
async def test_short_segments_are_not_reviewed(db_session, test_user, use_test_db):
    user, _ = test_user
    conv = await _conversation(db_session, user.id, [("user", "Hola")])
    structured = AsyncMock()
    with patch.object(focus_service.llm_adapter, "structured_output", structured):
        assert await focus_service.review_conversation(conv.id) == 0
    structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_update_and_delete(client, test_user, db_session):
    user, headers = test_user
    db_session.add_all(
        [
            FocusArea(
                user_id=user.id,
                target_language="en-US",
                concept="Articles",
                concept_key="articles",
                explanation="",
                examples=[],
                occurrences=4,
            ),
            FocusArea(
                user_id=user.id,
                target_language="en-US",
                concept="Tenses",
                concept_key="tenses",
                explanation="",
                examples=[],
                status="dismissed",
            ),
            FocusArea(
                user_id=user.id,
                target_language="fr-FR",
                concept="Genre",
                concept_key="genre",
                explanation="",
                examples=[],
            ),
        ]
    )
    await db_session.commit()
    res = await client.get("/api/focus-areas", headers=headers)
    assert res.status_code == 200
    [area] = res.json()
    assert area["concept"] == "Articles"
    assert area["mastery_streak"] == focus_service.MASTERY_STREAK

    res = await client.patch(
        f"/api/focus-areas/{area['id']}", json={"status": "mastered"}, headers=headers
    )
    assert res.json()["status"] == "mastered"
    assert (
        await client.delete(f"/api/focus-areas/{area['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get("/api/focus-areas", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_other_users_focus_areas_are_hidden(client, test_user, admin_user, db_session):
    _, headers = test_user
    other, _ = admin_user
    area = FocusArea(
        user_id=other.id,
        target_language="en-US",
        concept="X",
        concept_key="x",
        explanation="",
        examples=[],
    )
    db_session.add(area)
    await db_session.commit()
    res = await client.patch(
        f"/api/focus-areas/{area.id}", json={"status": "dismissed"}, headers=headers
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_drill_and_passing_practice(client, test_user, db_session):
    user, headers = test_user
    await make_study_plan(db_session, user_id=user.id, cefr_level="B1", is_active=True)
    area = FocusArea(
        user_id=user.id,
        target_language="en-US",
        concept="Past simple",
        concept_key="past simple",
        explanation="",
        examples=[],
    )
    db_session.add(area)
    await db_session.commit()
    drill = Drill(
        items=[
            DrillItem(sentence="Yesterday I ___ (go) home.", answer="went"),
            DrillItem(sentence="No blank here", answer="x"),
        ]
    )
    with patch.object(
        focus_service.llm_adapter, "structured_output", AsyncMock(return_value=drill)
    ):
        res = await client.post(f"/api/focus-areas/{area.id}/drill", headers=headers)
    assert res.status_code == 200
    assert [i["answer"] for i in res.json()["items"]] == ["went"]

    failed = await client.post(
        f"/api/focus-areas/{area.id}/practice", json={"correct": 2, "total": 5}, headers=headers
    )
    assert failed.json()["correct_streak"] == 0
    passed = await client.post(
        f"/api/focus-areas/{area.id}/practice", json={"correct": 5, "total": 5}, headers=headers
    )
    assert passed.json()["correct_streak"] == 1


@pytest.mark.asyncio
async def test_flashcards_from_focus_area_skip_duplicates(client, test_user, db_session):
    user, headers = test_user
    plan = await make_study_plan(db_session, user_id=user.id, cefr_level="B1", is_active=True)
    db_session.add(
        Flashcard(
            user_id=user.id,
            study_plan_id=plan.id,
            word="I went",
            definition="d",
            example_sentence="e",
            translation="t",
        )
    )
    area = FocusArea(
        user_id=user.id,
        target_language="en-US",
        concept="Past simple",
        concept_key="past simple",
        explanation="",
        examples=[],
    )
    db_session.add(area)
    await db_session.commit()
    cards = [
        FocusFlashcard(word="I went", definition="d", example_sentence="e", translation="t"),
        FocusFlashcard(word="She saw", definition="d", example_sentence="e", translation="t"),
    ]
    with patch.object(
        focus_service.llm_adapter,
        "structured_output",
        AsyncMock(return_value=focus_service.FocusFlashcards(cards=cards)),
    ):
        res = await client.post(f"/api/focus-areas/{area.id}/flashcards", headers=headers)
    assert res.json() == {"created": 1}
    sources = (
        (await db_session.execute(select(Flashcard.source).where(Flashcard.word == "She saw")))
        .scalars()
        .all()
    )
    assert sources == ["focus"]
