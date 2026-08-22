# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Renaming a type does not re-read what is filed under it.

M046 renamed the type used for planned events and re-read nothing, so the same
wrongly-filed rows carried a more confident name — and the question a user asks,
"what is coming up", reads exactly that set. M048 is the second half.

The trap this migration fell into first, and which these tests exist to hold:
asking the FULL classifier "what type is this" gets the wrong answer, because it
asks about opinion before it asks about plans. "We should deploy next Tuesday"
came back as an opinion and was demoted out of the upcoming list — a real
deadline, deleted from the one place a user looks for deadlines, by the
migration meant to clean that list up.

The question here is narrower: is this a plan? If yes it stays, whatever else it
also is.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage.migrations import (
    M048_upcoming_holds_only_what_is_upcoming as M048,
)

STILL_UPCOMING = [
    "The migration is scheduled for next Tuesday",
    "We should deploy next Tuesday",
    "I need the signed contract by Friday",
    "The weekly review is next Tuesday",
    "The TLS certificate expires on 2026-09-14",
    "Dentist appointment tomorrow at 10:30",
]

NOT_UPCOMING = [
    "The appointment was cancelled yesterday",
    "[codex] session ended (stop) at 2026-08-13 08:32 in memories",
    "The Git repository was updated on 2026-08-03",
    "We shipped the fix yesterday",
    "Paris is the capital of France",
    "The outage was caused by a stale DNS entry",
]


@pytest.fixture()
def store(tmp_path):
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute(
        "CREATE TABLE atomic_facts ("
        " fact_id TEXT PRIMARY KEY, content TEXT,"
        " fact_type TEXT NOT NULL DEFAULT 'semantic'"
        " CHECK (fact_type IN ('episodic','semantic','opinion','prospective')))"
    )
    conn.commit()
    yield conn
    conn.close()


def _seed(conn, texts, fact_type="prospective", start=0):
    for i, text in enumerate(texts, start=start):
        conn.execute(
            "INSERT INTO atomic_facts VALUES (?,?,?)", (f"f{i}", text, fact_type)
        )
    conn.commit()


def _types(conn) -> dict[str, str]:
    return {
        content: kind
        for content, kind in conn.execute("SELECT content, fact_type FROM atomic_facts")
    }


def test_a_real_deadline_is_not_demoted(store) -> None:
    """The defect this migration nearly shipped: deadlines removed as opinions."""
    _seed(store, STILL_UPCOMING)
    M048.apply(store)
    kinds = _types(store)
    wrong = {t: kinds[t] for t in STILL_UPCOMING if kinds[t] != "prospective"}
    assert not wrong, f"real upcoming work was demoted: {wrong}"


def test_what_is_not_a_plan_is_moved_out(store) -> None:
    _seed(store, NOT_UPCOMING)
    M048.apply(store)
    kinds = _types(store)
    still = {t: kinds[t] for t in NOT_UPCOMING if kinds[t] == "prospective"}
    assert not still, f"these are not upcoming and are still filed as such: {still}"


def test_a_demotion_never_lands_on_a_value_the_table_refuses(store) -> None:
    """A CHECK rejection mid-migration would roll the batch back forever."""
    _seed(store, NOT_UPCOMING + ["", "   ", "!!!", "2026-08-03"])
    M048.apply(store)   # must not raise
    kinds = set(_types(store).values())
    assert kinds <= {"episodic", "semantic", "opinion", "prospective"}


def test_rows_of_other_types_are_never_touched(store) -> None:
    _seed(store, ["Paris is the capital of France"], "semantic", start=0)
    _seed(store, ["I think tabs are better"], "opinion", start=10)
    _seed(store, ["I went to Paris last summer"], "episodic", start=20)
    before = _types(store)
    M048.apply(store)
    assert _types(store) == before


def test_running_it_twice_changes_nothing_the_second_time(store) -> None:
    _seed(store, STILL_UPCOMING + NOT_UPCOMING)
    M048.apply(store)
    first = _types(store)
    M048.apply(store)
    assert _types(store) == first
    assert M048.verify(store) is True


def test_verify_notices_a_pass_that_never_ran(store) -> None:
    """The check has to be able to fail, or it certifies nothing."""
    _seed(store, NOT_UPCOMING)
    assert M048.verify(store) is False
    M048.apply(store)
    assert M048.verify(store) is True


def test_more_rows_than_one_batch(store) -> None:
    _seed(store, NOT_UPCOMING * ((M048._BATCH // len(NOT_UPCOMING)) + 3))
    M048.apply(store)
    remaining = store.execute(
        "SELECT COUNT(*) FROM atomic_facts WHERE fact_type='prospective'"
    ).fetchone()[0]
    assert remaining == 0


def test_a_table_without_the_columns_is_not_an_error(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "bare.db")
    conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY)")
    conn.commit()
    M048.apply(conn)
    assert M048.verify(conn) is True
    conn.close()


def test_it_leaves_the_pre_rename_spelling_alone(store) -> None:
    """A store that has not run the rename yet is not this migration's business."""
    store.execute("DROP TABLE atomic_facts")
    store.execute(
        "CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, content TEXT,"
        " fact_type TEXT)"
    )
    store.execute(
        "INSERT INTO atomic_facts VALUES ('t1','session ended at 10:00','temporal')"
    )
    store.commit()
    M048.apply(store)
    assert store.execute(
        "SELECT fact_type FROM atomic_facts WHERE fact_id='t1'"
    ).fetchone()[0] == "temporal"
