"""
Language-aware grammar dispatcher.

Mirrors the vocabulary/phrasebook dispatcher pattern: resolves the correct
per-language grammar module by ISO 639-1 prefix.
"""

from __future__ import annotations

import sys

from app.data._types import GrammarExample, GrammarMistake, GrammarTopic  # noqa: F401

_LANG_MODULES: dict[str, str] = {
    "en-GB": "app.data.en_GB.grammar",
    "en-US": "app.data.en_US.grammar",
    "de": "app.data.de.grammar",
    "es": "app.data.es.grammar",
    "fr": "app.data.fr.grammar",
    "it": "app.data.it.grammar",
    "ja": "app.data.ja.grammar",
    "ko": "app.data.ko.grammar",
    "pt": "app.data.pt.grammar",
    "zh": "app.data.zh.grammar",
}

_CACHE: dict[str, list[GrammarTopic]] = {}


def _resolve_topics(target_language: str) -> list[GrammarTopic]:
    module_name = _LANG_MODULES.get(target_language) or _LANG_MODULES.get(
        target_language.split("-")[0], "app.data.en_GB.grammar"
    )

    if module_name not in _CACHE:
        __import__(module_name)
        _CACHE[module_name] = sys.modules[module_name].GRAMMAR_TOPICS

    return _CACHE[module_name]


def get_grammar_topics(target_language: str = "en-GB") -> list[GrammarTopic]:
    """Return all grammar topics for the given target language."""
    return _resolve_topics(target_language)


def get_grammar_topic(slug: str, target_language: str = "en-GB") -> GrammarTopic | None:
    """Return a single grammar topic by slug for the given target language."""
    topics = _resolve_topics(target_language)
    for t in topics:
        if t.slug == slug:
            return t
    return None


def get_known_mistakes(
    slugs: list[str],
    target_language: str = "en-GB",
    per_topic: int = 3,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Return curated common mistakes for the given grammar topics, in slug order.

    Takes up to ``per_topic`` from each topic, round-robin across topics, so a
    unit with many grammar points still covers each one before the ``limit``.
    """
    pools: list[list[dict[str, str]]] = []
    for slug in dict.fromkeys(slugs):
        topic = get_grammar_topic(slug, target_language)
        if topic is None or not topic.common_mistakes:
            continue
        pools.append(
            [
                {
                    "topic": topic.title,
                    "wrong": mistake.wrong,
                    "correct": mistake.correct,
                    "note": mistake.note,
                }
                for mistake in topic.common_mistakes[:per_topic]
            ]
        )
    mistakes: list[dict[str, str]] = []
    for round_index in range(per_topic):
        for pool in pools:
            if round_index < len(pool):
                mistakes.append(pool[round_index])
                if len(mistakes) >= limit:
                    return mistakes
    return mistakes
