"""Curated unit word lists reach lesson generation (roadmap R4)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.data.vocabulary import get_unit_vocabulary, get_vocabulary_set
from app.services.prompts.lesson import build_lesson_generation_prompt, build_unit_vocabulary_block


def test_unit_vocabulary_returns_topic_and_words() -> None:
    saludos = get_vocabulary_set("saludos_a1", "es-ES")
    assert get_unit_vocabulary(["saludos_a1"], "es-ES") == [
        (saludos.topic, [entry.word for entry in saludos.words])
    ]


def test_unit_vocabulary_limits_skips_unknown_and_dedupes() -> None:
    assert get_unit_vocabulary(["no-such-set"], "es-ES") == []
    assert len(get_unit_vocabulary(["saludos_a1", "saludos_a1"], "es-ES")) == 1
    limited = get_unit_vocabulary(["saludos_a1", "numeros_a1"], "es-ES", limit=5)
    assert len(limited) == 1
    assert len(limited[0][1]) == 5


def test_unit_vocabulary_block_only_when_words_exist() -> None:
    assert build_unit_vocabulary_block(None) == ""
    assert build_unit_vocabulary_block([]) == ""
    block = build_unit_vocabulary_block([("Saludos", ["hola", "adiós"])])
    assert "<<<UNIT_VOCABULARY\n- Saludos: hola, adiós\nUNIT_VOCABULARY" in block


def test_lesson_prompt_includes_unit_vocabulary_only_when_given() -> None:
    common = dict(
        cefr_level="A1",
        target_language_name="Spanish",
        lesson_type="vocabulary",
        topic="Saludos",
        unit_id="a1-unit-1",
        grammar_points="ser",
        vocabulary_set_ids="saludos_a1",
        week=1,
        day=1,
        valid_slugs="ser",
    )
    assert "<<<UNIT_VOCABULARY" not in build_lesson_generation_prompt(**common)
    prompt = build_lesson_generation_prompt(**common, unit_vocabulary=[("Saludos", ["hola"])])
    assert "- Saludos: hola" in prompt
    assert prompt.index("<<<UNIT_VOCABULARY") < prompt.index("STRICT CONSTRAINTS")


@pytest.mark.asyncio
async def test_generate_lesson_sends_the_unit_word_lists() -> None:
    from app.schemas.lessons import LessonContent
    from app.services.lesson_generator import generate_lesson

    content = LessonContent(
        lesson_type="vocabulary",
        title="t",
        cefr_level="A1",
        explanation={"text": "t", "key_points": [], "examples": []},
        exercises=[],
    )
    mock_llm = AsyncMock(return_value=content)
    with patch("app.services.lesson_generator.llm_adapter.structured_output", mock_llm):
        await generate_lesson(
            cefr_level="A1",
            lesson_type="vocabulary",
            topic="Saludos",
            week=1,
            day=1,
            unit_id="a1-unit-1",
            vocabulary_set_ids=["saludos_a1", "numeros_a1"],
            target_language="es-ES",
        )

    prompt = mock_llm.await_args.args[0][0]["content"]
    for set_id in ("saludos_a1", "numeros_a1"):
        vocab_set = get_vocabulary_set(set_id, "es-ES")
        assert f"- {vocab_set.topic}: {vocab_set.words[0].word}," in prompt
