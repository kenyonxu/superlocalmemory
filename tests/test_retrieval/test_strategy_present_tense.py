# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for present-tense and recency query classification in strategy.py.

Covers:
  - Present-tense words (now, today, yesterday, currently) classify
    as temporal or recency — not factual.
  - Present-activity phrases ("working on", "at the moment", etc.) classify
    as recency, not factual.
  - Plainly factual queries without temporal/recency signals stay factual.
  - No factual query without a temporal or recency signal becomes recency.
  - classify_query() module-level convenience function is importable.
  - STRATEGY_PRESETS includes a "recency" key with the expected channel weights.
"""

from __future__ import annotations

import pytest

from superlocalmemory.retrieval.strategy import (
    STRATEGY_PRESETS,
    QueryStrategyClassifier,
    classify_query,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def clf() -> QueryStrategyClassifier:
    return QueryStrategyClassifier()


def _type(clf: QueryStrategyClassifier, query: str) -> str:
    return clf.classify(query, {}).query_type


# ---------------------------------------------------------------------------
# Table-driven: present-tense temporal words → temporal or recency (not factual)
# These FAIL before the _TEMPORAL_WORDS extension (task 2.1).
# ---------------------------------------------------------------------------

PRESENT_TEMPORAL_WORDS = [
    ("now",       "what did I do now"),
    ("today",     "what did I do today"),
    ("yesterday", "what did I do yesterday"),
    ("currently", "what am I currently focused on"),
    ("tonight",   "what did I do tonight"),
    ("tomorrow",  "what is happening tomorrow"),
    ("latest",    "what is the latest update"),
]


@pytest.mark.parametrize("word,query", PRESENT_TEMPORAL_WORDS, ids=[w for w, _ in PRESENT_TEMPORAL_WORDS])
def test_present_temporal_word_is_not_factual(
    clf: QueryStrategyClassifier, word: str, query: str
) -> None:
    """Each present-tense temporal word must classify as temporal or recency."""
    result = _type(clf, query)
    assert result in ("temporal", "recency"), (
        f"word {word!r} in {query!r} -> {result!r}; expected temporal or recency"
    )


# ---------------------------------------------------------------------------
# Table-driven: recency phrases → recency (not factual)
# These FAIL before the _RECENCY_PHRASES + recency strategy (task 2.2).
# ---------------------------------------------------------------------------

RECENCY_QUERIES = [
    # These must NOT contain temporal words so the recency phrase check fires.
    "what am I working on",
    "what are we working on at the moment",
    "what's happening these days",
    "what am i doing these days",       # "these days" phrase, no temporal word
    "what have i been working on",
    "what are we working on",           # "working on" phrase, no temporal word
]


@pytest.mark.parametrize("query", RECENCY_QUERIES)
def test_recency_phrase_classifies_as_recency(
    clf: QueryStrategyClassifier, query: str
) -> None:
    """Present-activity phrase queries must classify as recency."""
    result = _type(clf, query)
    assert result == "recency", (
        f"{query!r} -> {result!r}; expected recency"
    )


# ---------------------------------------------------------------------------
# Regression: plainly factual queries must NOT become recency or temporal.
# Over-triggering is the critical risk — these guard against it.
# ---------------------------------------------------------------------------

FACTUAL_QUERIES = [
    ("what is the architecture",       "factual"),
    ("what is the database schema",    "factual"),
    ("who maintains this codebase",    "factual"),
    ("who wrote the retrieval module", "factual"),
    ("what happened in march",         "temporal"),  # retrospective, not recency
    ("when did we deploy",             "temporal"),  # retrospective, not recency
    ("who was at the meeting",         "factual"),
    ("where is the config file",       "factual"),
    ("how does authentication work",   "factual"),
]


# A word that hints at time does not make a question about a topic.
# "current" was originally in the present-tense set, so "what is the current
# database schema" was routed to the time-aware path — which weights
# word-matching MORE heavily than the topical path, amplifying the exact signal
# that made this release necessary. These are the forms an audit found missing
# from the regression table: time-flavoured wording, topical intent.
TIME_FLAVOURED_BUT_TOPICAL = [
    "what is the current database schema",
    "what is the current state of the migration",
    "what is the current architecture",
]


@pytest.mark.parametrize("query", TIME_FLAVOURED_BUT_TOPICAL)
def test_time_flavoured_topical_question_stays_factual(query: str) -> None:
    got = classify_query(query).query_type
    assert got == "factual", (
        f"{query!r} classified as {got!r}. It asks about a topic; routing it to a "
        f"time-aware strategy boosts word-matching above the topical preset and "
        f"answers a question about the schema with whatever is newest"
    )


@pytest.mark.parametrize("query,expected", FACTUAL_QUERIES, ids=[q for q, _ in FACTUAL_QUERIES])
def test_factual_query_not_over_triggered(
    clf: QueryStrategyClassifier, query: str, expected: str
) -> None:
    """Plainly factual or retrospective queries must not become recency."""
    result = _type(clf, query)
    assert result == expected, (
        f"{query!r} -> {result!r}; expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# Gate-compatibility: classify_query module-level function
# The gate calls: from superlocalmemory.retrieval.strategy import classify_query
# ---------------------------------------------------------------------------

class TestClassifyQueryFunction:
    def test_importable(self) -> None:
        """classify_query must be importable at module level."""
        # Already imported at module level; this checks that import path works.
        assert callable(classify_query)

    def test_returns_query_strategy(self) -> None:
        from superlocalmemory.retrieval.strategy import QueryStrategy
        result = classify_query("what am I working on")
        assert isinstance(result, QueryStrategy)

    def test_mandatory_words_via_classify_query(self) -> None:
        """Gate condition 2: five mandatory words classify as temporal or recency."""
        mandatory = ("now", "today", "yesterday", "currently")
        for w in mandatory:
            qt = classify_query(f"what did I do {w}").query_type
            assert qt in ("temporal", "recency"), (
                f"word {w!r} -> {qt!r}; expected temporal or recency"
            )

    def test_present_activity_via_classify_query(self) -> None:
        """Gate condition 3: present-activity query classifies as recency."""
        qt = classify_query("what am I working on").query_type
        assert qt == "recency", f"expected recency, got {qt!r}"


# ---------------------------------------------------------------------------
# STRATEGY_PRESETS: recency key and channel weights
# ---------------------------------------------------------------------------

class TestRecencyPreset:
    def test_recency_key_exists(self) -> None:
        assert "recency" in STRATEGY_PRESETS, "STRATEGY_PRESETS missing 'recency' key"

    def test_recency_temporal_weight_highest(self) -> None:
        preset = STRATEGY_PRESETS["recency"]
        assert preset.get("temporal", 0.0) >= 2.0, (
            "recency preset: temporal weight must be >= 2.0 (time proximity should dominate)"
        )

    def test_recency_bm25_weight_present(self) -> None:
        preset = STRATEGY_PRESETS["recency"]
        assert "bm25" in preset

    def test_recency_hopfield_damped(self) -> None:
        """Hopfield should be damped for recency — pattern completion is noise here."""
        preset = STRATEGY_PRESETS["recency"]
        assert preset.get("hopfield", 1.0) <= 0.6, (
            "recency preset: hopfield weight should be <= 0.6 (damped for recency)"
        )

    def test_recency_weights_are_floats(self) -> None:
        for k, v in STRATEGY_PRESETS["recency"].items():
            assert isinstance(v, float), f"recency preset key {k!r} value must be float"
