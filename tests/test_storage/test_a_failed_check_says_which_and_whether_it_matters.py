# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Two things a failing migration check owes the person reading about it.

Both come from one report where a daemon was unusable for days (#125).

FIRST — WHICH CHECK

A completed migration is re-verified on every start. When that stops passing the
runner said ``safe repair did not restore M043_quarantine_display_summaries``.
M043 checks five separate things and that sentence names none of them, so the
person who hit it had to come back and ask which — and so did we. The reason
string now carries the migration's own account of the specific condition.

SECOND — WHETHER IT SHOULD STOP THE DAEMON

Any failed migration returned 503 on every route except health and status. For a
missing table that is right: the queries would hit something that is not there.
But three of M043's five checks are about *data* — a summary that should be
withheld is not, or a real memory is hidden — and ordinary use can re-violate
them, a single consolidation pass being enough. So one drifted row could make a
working store unreachable indefinitely, with a manual restart the only way out
and nothing for the restart to fix.

The distinction is now the migration's to declare, and anything that does not
declare is still treated as blocking.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage.migrations import (
    M043_quarantine_display_summaries as m043,
)


def _store(tmp_path, *, quarantined=True, summaries=True):
    """A store with the schema halves of M043's end-state present or absent."""
    conn = sqlite3.connect(tmp_path / "memory.db")
    cols = "fact_id TEXT PRIMARY KEY, profile_id TEXT, memory_id TEXT, content TEXT"
    if quarantined:
        cols += ", quarantined INTEGER NOT NULL DEFAULT 0"
    conn.execute(f"CREATE TABLE atomic_facts ({cols})")
    if summaries:
        conn.execute(
            "CREATE TABLE consolidated_summaries ("
            " summary_id TEXT PRIMARY KEY, profile_id TEXT, content TEXT)"
        )
    conn.commit()
    return conn


class TestItNamesTheCheckThatFailed:
    def test_a_missing_column_is_named(self, tmp_path) -> None:
        conn = _store(tmp_path, quarantined=False)
        try:
            reason = m043.unmet(conn)
            assert reason, "verify fails here, so a reason is owed"
            assert "quarantined" in reason
        finally:
            conn.close()

    def test_a_missing_display_table_is_named(self, tmp_path) -> None:
        conn = _store(tmp_path, summaries=False)
        try:
            assert "consolidated_summaries" in m043.unmet(conn)
        finally:
            conn.close()

    def test_a_store_in_good_order_owes_no_reason(self, tmp_path) -> None:
        """The control. A reason on a healthy store would be a false alarm on
        every start."""
        conn = _store(tmp_path)
        try:
            assert m043.unmet(conn) == ""
        finally:
            conn.close()

    def test_verify_and_the_reason_can_never_disagree(self, tmp_path) -> None:
        """``verify()`` is a wrapper over ``unmet()`` on purpose. Two
        independent definitions of "verified" is exactly the drift that put a
        migration in the state this issue is about."""
        for kwargs in ({}, {"quarantined": False}, {"summaries": False}):
            conn = _store(tmp_path, **kwargs)
            try:
                assert m043.verify(conn) is (m043.unmet(conn) == "")
            finally:
                conn.close()
            for f in tmp_path.glob("memory.db*"):
                f.unlink()


class TestOnlyAMissingSchemaStopsTheDaemon:
    def test_a_missing_column_blocks_serving(self, tmp_path) -> None:
        conn = _store(tmp_path, quarantined=False)
        try:
            assert m043.blocks_serving(conn) is True
        finally:
            conn.close()

    def test_a_missing_display_table_blocks_serving(self, tmp_path) -> None:
        conn = _store(tmp_path, summaries=False)
        try:
            assert m043.blocks_serving(conn) is True
        finally:
            conn.close()

    def test_data_drift_does_not_block_serving(self, tmp_path) -> None:
        """The heart of it. The schema is all there; some row drifted. Every
        route works, so refusing them all is the outage, not the cure."""
        conn = _store(tmp_path)
        try:
            assert m043.blocks_serving(conn) is False
        finally:
            conn.close()


class TestTheGateAsksBeforeRefusing:
    def test_nothing_failed_means_nothing_blocked(self) -> None:
        from superlocalmemory.server.unified_daemon import _serving_blocked_by

        assert _serving_blocked_by({"failed": []}) == []
        assert _serving_blocked_by({}) == []

    def test_a_migration_that_does_not_declare_is_still_blocking(self) -> None:
        """Fail-closed. Opting out has to be deliberate, per migration, or this
        change would quietly open a door for every migration nobody has looked
        at."""
        from superlocalmemory.server.unified_daemon import _serving_blocked_by

        assert _serving_blocked_by(
            {"failed": ["M999_no_such_migration"]}
        ) == ["M999_no_such_migration"]

    def test_a_data_only_failure_does_not_block(self, tmp_path, monkeypatch) -> None:
        """The store is built here rather than borrowed from the machine.

        The first version of this asserted against whatever store the running
        machine happened to have, which passes or fails on ambient state and
        tests nothing reliably.
        """
        monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
        conn = _store(tmp_path)          # schema complete, so only data can drift
        conn.close()

        from superlocalmemory.server.unified_daemon import _serving_blocked_by

        blocked = _serving_blocked_by(
            {"failed": ["M043_quarantine_display_summaries"]}
        )
        assert blocked == [], (
            "a data invariant must not 503 a store whose schema is complete"
        )

    def test_a_missing_schema_still_blocks_through_the_gate(
        self, tmp_path, monkeypatch,
    ) -> None:
        """The control on the control. If the gate said "never block" this whole
        change would be a way to serve requests against a store that cannot
        answer them."""
        monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
        conn = _store(tmp_path, quarantined=False)
        conn.close()

        from superlocalmemory.server.unified_daemon import _serving_blocked_by

        assert _serving_blocked_by(
            {"failed": ["M043_quarantine_display_summaries"]}
        ) == ["M043_quarantine_display_summaries"]


class TestTheRunnerRepeatsTheReason:
    def test_it_asks_the_migration_and_survives_one_that_cannot_answer(self) -> None:
        from superlocalmemory.storage._migration_internals import _why_unmet

        class Silent:
            pass

        class Raises:
            @staticmethod
            def unmet(conn):
                raise RuntimeError("cannot tell")

        class Answers:
            @staticmethod
            def unmet(conn):
                return "three rows drifted"

        assert _why_unmet(Silent(), None) == ""
        assert _why_unmet(Raises(), None) == "", (
            "reporting a failure must not become a second failure"
        )
        assert _why_unmet(Answers(), None) == "three rows drifted"
