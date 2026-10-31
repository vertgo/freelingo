"""Curated grammar mistakes reach lesson generation (roadmap R3)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.data.grammar import get_grammar_topic, get_known_mistakes
from app.services.prompts.lesson import build_known_mistakes_block, build_lesson_generation_prompt


def test_known_mistakes_round_robin_across_topics() -> None:
    mistakes = get_known_mistakes(["ser", "estar"], "es-ES")
    ser = get_grammar_topic("ser", "es-ES").common_mistakes
    estar = get_grammar_topic("estar", "es-ES").common_mistakes
    assert [m["wrong"] for m in mistakes[:2]] == [ser[0].wrong, estar[0].wrong]
    assert mistakes[0] == {
        "topic": "El verbo ser",
        "wrong": ser[0].wrong,
        "correct": ser[0].correct,
        "note": ser[0].note,
    }


def test_known_mistakes_limits_skips_unknown_and_dedupes() -> None:
    assert get_known_mistakes(["no-such-topic"], "es-ES") == []
    assert len(get_known_mistakes(["ser", "estar"], "es-ES", limit=3)) == 3
    assert len(get_known_mistakes(["ser", "ser"], "es-ES", per_topic=1)) == 1


def test_known_mistakes_block_only_when_mistakes_exist() -> None:
    assert build_known_mistakes_block(None) == ""
    assert build_known_mistakes_block([]) == ""
    block = build_known_mistakes_block(
        [{"topic": "El verbo ser", "wrong": "Estoy de X.", "correct": "Soy de X.", "note": "n"}]
    )
    assert "KNOWN LEARNER MISTAKES" in block
    assert "- [El verbo ser] ✗ Estoy de X. → ✓ Soy de X. (n)" in block


def _prompt(**extra) -> str:
    return build_lesson_generation_prompt(
        cefr_level="A1",
        target_language_name="Spanish",
        lesson_type="grammar",
        topic="Presentarse",
        unit_id="a1-unit-1",
        grammar_points="ser",
        vocabulary_set_ids="saludos_a1",
        week=1,
        day=1,
        valid_slugs="ser",
        **extra,
    )


def test_lesson_prompt_includes_known_mistakes_only_when_given() -> None:
    assert "KNOWN_MISTAKES" not in _prompt()
    prompt = _prompt(
        known_mistakes=[{"topic": "T", "wrong": "Estoy de X.", "correct": "Soy de X.", "note": "n"}]
    )
    assert "Estoy de X." in prompt
    assert prompt.index("KNOWN_MISTAKES") < prompt.index("STRICT CONSTRAINTS")


@pytest.mark.asyncio
async def test_generate_lesson_sends_curated_mistakes_for_unit_grammar() -> None:
    from app.schemas.lessons import LessonContent
    from app.services.lesson_generator import generate_lesson

    content = LessonContent(
        lesson_type="grammar",
        title="t",
        cefr_level="A1",
        unit_id="a1-unit-1",
        explanation={"text": "t", "key_points": [], "examples": []},
        exercises=[],
        vocabulary=[],
        grammar_refs=[],
    )
    mock_llm = AsyncMock(return_value=content)
    with patch("app.services.lesson_generator.llm_adapter.structured_output", mock_llm):
        await generate_lesson(
            cefr_level="A1",
            lesson_type="grammar",
            topic="Presentarse",
            week=1,
            day=1,
            unit_id="a1-unit-1",
            grammar_points=["ser", "estar"],
            target_language="es-ES",
        )

    prompt = mock_llm.await_args.args[0][0]["content"]
    assert "KNOWN LEARNER MISTAKES" in prompt
    assert get_grammar_topic("ser", "es-ES").common_mistakes[0].wrong in prompt
    assert get_grammar_topic("estar", "es-ES").common_mistakes[0].wrong in prompt
