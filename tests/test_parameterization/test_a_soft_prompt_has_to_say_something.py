# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Soft prompts are injected into the model's context on every turn.

That makes an empty one worse than none: it spends the budget and asserts
something false about the user. Measured on a live store, the two prompts
actually being injected were:

    "The user's preferred technology stack includes: test, gate, practices,
     compliance, projects, while, their, processing, data."

    "The user typically When general workflow, frequently uses Bash (324
     times...); When when using up-stdio__get_image, ..."

The first names no technology. The second contains real signal wrapped in
broken grammar. Both are covered below by their exact live text, because a test
written against a tidied-up paraphrase is a test against a case that never
happened.
"""

from __future__ import annotations

import pytest

from superlocalmemory.parameterization.soft_prompt_generator import (
    _fix_stutter,
    _is_substantive,
)

# Verbatim from the live store, 2026-08-22.
_LIVE_TECH = (
    "test, gate, practices, compliance, projects, while, their, "
    "processing, data"
)
_LIVE_WORKFLOW = (
    "The user typically When general workflow, frequently uses Bash "
    "(324 times, 20% of all tool usage); When when using "
    "up-stdio__get_image, typically follow with up-http__echo_text"
)


# ---------------------------------------------------------------------------
# What counts as saying something
# ---------------------------------------------------------------------------

def test_a_prompt_made_only_of_filler_words_is_rejected():
    """The remaining floor: a sentence built from words that mean nothing."""
    assert not _is_substantive(
        "tech_preference", {"technologies": "their, while, them, these"})


def test_nothing_substituted_means_nothing_to_say():
    assert not _is_substantive("tech_preference", {"technologies": ""})
    assert not _is_substantive("identity", {})


REAL_VALUES = [
    # Every one of these was discarded by an earlier version of this filter,
    # which required a technology claim to name something from a fixed
    # vocabulary. The first four are the GENUINE rows on a live store.
    ("tech_preference", "Node.js"),
    ("tech_preference", "Git"),
    ("tech_preference", "pip"),
    ("tech_preference", "Go"),
    ("tech_preference", "npm"),
    ("tech_preference", "C++"),
    ("tech_preference", "zig, gleam"),      # a stack no list will ever enumerate
    ("avoidance", "AWS"),
    ("communication_style", "brief"),        # 5 characters, and a real preference
]


@pytest.mark.parametrize("category,value", REAL_VALUES)
def test_a_real_preference_is_never_discarded(category, value):
    """A filter that drops real preferences to catch fake ones is worse.

    Worse specifically because nothing reports it: the prompt is simply not
    stored, so the user sees a profile that has quietly forgotten what they use.
    """
    keys = {"technologies": value, "avoid_list": value, "style": value}
    assert _is_substantive(category, keys), (
        f"{value!r} was discarded as saying nothing about the user"
    )


def test_a_short_title_with_a_short_employer_still_injects():
    """"CEO", "AI" and "IBM" are all shorter than any sensible threshold."""
    assert _is_substantive(
        "identity", {"role": "CEO", "domains": "AI", "organization": "IBM"})


def test_the_filter_is_no_longer_the_thing_protecting_this():
    """The real fix was upstream, and this records why the filter got weaker.

    The live nonsense came from word-frequency topics being routed into the
    technology-preference category. They now have their own category, where the
    same words are a true statement — so the filter no longer has to tell a
    frequent word from a tool name, which is a distinction it could not make.
    """
    from superlocalmemory.parameterization.pattern_extractor import (
        _BEHAVIORAL_TYPE_MAP,
    )

    assert _BEHAVIORAL_TYPE_MAP["interest"] == "topic_interest"


def test_the_duplicated_conjunction_is_removed():
    fixed = _fix_stutter(_LIVE_WORKFLOW)
    assert "When when" not in fixed
    assert "typically When" not in fixed


def test_the_real_signal_survives_the_grammar_fix():
    """The workflow prompt is worth keeping — it is only badly worded."""
    fixed = _fix_stutter(_LIVE_WORKFLOW)
    assert "Bash" in fixed
    assert "324 times" in fixed
    assert "up-http__echo_text" in fixed


def test_a_correctly_worded_prompt_is_left_alone():
    clean = "The user prefers concise responses. Avoid preamble."
    assert _fix_stutter(clean) == clean


# ---------------------------------------------------------------------------
# The gate this task was measured against
# ---------------------------------------------------------------------------

def test_storing_a_prompt_supersedes_rather_than_accumulates():
    """Why a row count cannot measure what procedural memory has learned.

    Storing a prompt deactivates the previous one for its category and inserts a
    new version, so the row count rises by one per category on every
    consolidation cycle whether or not anything was learned. On a live store it
    read 34 — two categories times seventeen cycles — with exactly two rows
    active and both of them junk.

    Driven through the real writer rather than asserted against its SQL text: the
    earlier version of this test read the injector's source for "SET active = 0",
    which stays true however the writer behaves.
    """
    import sqlite3
    import tempfile
    from pathlib import Path

    from superlocalmemory.parameterization.prompt_injector import PromptInjector
    from superlocalmemory.parameterization.soft_prompt_generator import (
        SoftPromptTemplate,
    )

    db_path = Path(tempfile.mkdtemp()) / "memory.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE soft_prompt_templates ("
            " prompt_id TEXT PRIMARY KEY, profile_id TEXT, category TEXT,"
            " content TEXT, source_pattern_ids TEXT, confidence REAL,"
            " effectiveness REAL, token_count INTEGER, retention_score REAL,"
            " active INTEGER, version INTEGER, created_at TEXT, updated_at TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    class _Db:
        def execute(self, sql, params=()):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(sql, params).fetchall()
                c.commit()
                return rows
            finally:
                c.close()

    from superlocalmemory.core.config import ParameterizationConfig
    from superlocalmemory.parameterization.soft_prompt_generator import (
        SoftPromptGenerator,
    )

    config = ParameterizationConfig()
    injector = PromptInjector(_Db(), SoftPromptGenerator(config), config)

    def _template(n: int) -> SoftPromptTemplate:
        return SoftPromptTemplate(
            prompt_id=f"p{n}", profile_id="default",
            category="tech_preference", content=f"version {n}",
            source_pattern_ids=[], confidence=1.0, effectiveness=0.5,
            token_count=0, retention_score=1.0, active=True, version=1,
        )

    for n in range(4):
        injector.store_prompts([_template(n)])

    conn = sqlite3.connect(str(db_path))
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM soft_prompt_templates").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM soft_prompt_templates WHERE active=1"
        ).fetchone()[0]
    finally:
        conn.close()

    assert total == 4, f"expected one row per store, got {total}"
    assert active == 1, (
        f"{active} rows are active; the count of STORED prompts is therefore "
        f"version history, and cannot measure what was learned"
    )
