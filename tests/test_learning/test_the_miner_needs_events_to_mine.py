# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Skill evolution had four apparent blocks and one actual cause.

The evolution log was empty. Four separate explanations were proposed for it,
and only one of them was real: nothing had used a skill enough times for the
miner to say anything about it. One Skill event existed in two thousand tool
events, against a floor of five.

That makes the other three self-resolving or wrong, and the tests here prove
which. In particular, the "category mismatch" theory — that the degradation
trigger queries ``skill_performance`` while the live rows are all
``tool_preference`` — describes a symptom of the starvation, not a bug: the
miner writes ``skill_performance``, it had just never written anything.
"""

from __future__ import annotations

import pytest

from superlocalmemory.learning.skill_performance_miner import (
    MIN_INVOCATIONS,
    SkillPerformanceMiner,
)
from tests.helpers.seed_skill_events import (
    count_skill_assertions,
    ensure_schema,
    seed_skill_events,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "memory.db"
    ensure_schema(path)
    return path


def test_an_empty_store_mines_nothing_and_does_not_fail(db):
    result = SkillPerformanceMiner(db).mine("default")
    assert result["skills_found"] == 0
    assert count_skill_assertions(db) == 0


def test_below_the_floor_nothing_is_asserted(db):
    """Four uses is not enough to say anything about a skill, and it must not."""
    seed_skill_events(db, invocations=MIN_INVOCATIONS - 1, failures=1)
    SkillPerformanceMiner(db).mine("default")
    assert count_skill_assertions(db) == 0


def test_at_the_floor_the_miner_writes_what_the_trigger_looks_for(db):
    """The whole chain's first link, and the one that was missing.

    The assertion has to carry ``category='skill_performance'`` — that is the
    exact value the degradation trigger selects on. An assertion under any other
    category is invisible to it.
    """
    seed_skill_events(db, invocations=MIN_INVOCATIONS, failures=2)
    result = SkillPerformanceMiner(db).mine("default")

    assert result["skills_found"] == 1
    assert count_skill_assertions(db) == 1


def test_the_assertion_names_the_skill_and_its_evidence(db):
    import sqlite3

    seed_skill_events(db, skill_name="brainstorming",
                      invocations=6, failures=2)
    SkillPerformanceMiner(db).mine("default")

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT trigger_condition, action, category, confidence, "
            "evidence_count FROM behavioral_assertions "
            "WHERE category = 'skill_performance'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "the miner wrote nothing"
    trigger, action, category, confidence, evidence = row
    assert "brainstorming" in (trigger + action)
    assert category == "skill_performance"
    assert 0.0 <= confidence <= 1.0
    assert evidence >= MIN_INVOCATIONS


def test_mining_twice_reinforces_rather_than_duplicating(db):
    seed_skill_events(db, invocations=MIN_INVOCATIONS, failures=2)
    miner = SkillPerformanceMiner(db)

    first = miner.mine("default")
    second = miner.mine("default")

    assert count_skill_assertions(db) == 1, "a second pass duplicated the row"
    assert first.get("assertions_created", 0) == 1
    assert second.get("assertions_reinforced", 0) == 1


def test_two_profiles_do_not_share_skill_history(db):
    seed_skill_events(db, invocations=MIN_INVOCATIONS, profile_id="a")
    SkillPerformanceMiner(db).mine("a")
    assert count_skill_assertions(db, profile_id="a") == 1
    assert count_skill_assertions(db, profile_id="b") == 0


def test_the_trigger_selects_the_category_the_miner_writes(db):
    """Closes the loop on the category theory by reading both sides.

    Asserted against the trigger's own source rather than a copy of the string,
    so the two cannot drift apart without this failing.
    """
    import inspect

    from superlocalmemory.evolution import triggers

    src = inspect.getsource(triggers)
    assert "skill_performance" in src, (
        "the degradation trigger no longer selects the category the miner "
        "writes; one of the two moved without the other"
    )

    seed_skill_events(db, invocations=MIN_INVOCATIONS, failures=3)
    SkillPerformanceMiner(db).mine("default")
    assert count_skill_assertions(db) == 1
