# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""A defective store has to fix itself, because most owners will not fix it.

About three quarters of this product's users are not engineers. The repair for
a poisoned store therefore cannot be a runbook, a repository clone or a SQL
snippet -- it has to happen on the next start, on pip, npm and source installs
alike, with nobody asked to do anything.

M043 is that repair. These tests pin the three things it must never get wrong:

  * it must PRESERVE before it WITHHOLDS, so a summary the owner can see today
    is still visible tomorrow;
  * its predicates must be exact, so no genuine memory is swept up;
  * it must be safe to run on every start forever, which means idempotent and
    self-limiting.

Plus the enforcement half: a withheld row must be unreachable through every
channel, not merely marked.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.storage.migrations import (
    M043_quarantine_display_summaries as M043,
)
from superlocalmemory.storage.schema import create_all_tables

_PROFILE = "default"


def _poisoned_store(path: Path) -> dict[str, int]:
    """A store in the exact shape 4.0.9 left behind.

    Deliberately built with a bare sqlite3 connection and no
    ``PRAGMA foreign_keys``, because that is how the rows got there: the
    consolidator wrote through ``storage/memory_write.py``, which sets
    busy_timeout and nothing else, so the declared FK from atomic_facts to
    memories never refused the ``memory_id=''`` row.
    """
    conn = sqlite3.connect(str(path))
    create_all_tables(conn)
    # create_all_tables sets foreign_keys=ON. Turn it back OFF to reproduce the
    # bug, because that is literally how the rows got there and this fixture
    # would otherwise be impossible to build: with the constraint enforced,
    # SQLite refuses `memory_id=''` outright ("FOREIGN KEY constraint failed").
    # The consolidator wrote through storage/memory_write.py, which sets
    # busy_timeout and nothing else.
    # PRAGMA foreign_keys is a NO-OP inside a transaction (SQLite docs), and
    # create_all_tables leaves one open, so this has to commit first. Getting
    # that wrong is silent: the pragma appears to run and the constraint stays on.
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0, (
        "the fixture cannot reproduce the bug with foreign keys enforced"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fact_consolidations ("
        " consolidation_id TEXT PRIMARY KEY, profile_id TEXT,"
        " consolidated_fact_id TEXT NOT NULL, source_fact_ids TEXT NOT NULL,"
        " strategy TEXT, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fact_retention ("
        " fact_id TEXT PRIMARY KEY, profile_id TEXT,"
        " retention_score REAL, memory_strength REAL, access_count INTEGER,"
        " last_accessed_at TEXT, last_computed_at TEXT, lifecycle_zone TEXT)"
    )
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content) VALUES "
        "('mem1', ?, 'a real conversation')", (_PROFILE,),
    )

    # Three genuine memories, archived by consolidation while scored to keep.
    for i in range(3):
        fid = f"real-{i}"
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
            " lifecycle, created_at) VALUES (?, 'mem1', ?, ?, 'archived', ?)",
            (fid, _PROFILE, f"Varun decided release {i} ships on a Tuesday.",
             f"2026-0{i + 1}-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO fact_retention (fact_id, profile_id, retention_score,"
            " lifecycle_zone) VALUES (?, ?, 1.0, 'archive')", (fid, _PROFILE),
        )

    # One genuine memory legitimately archived: a LOW retention score. The
    # repair must not touch it, or "restore what was wrongly hidden" quietly
    # becomes "un-forget everything".
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
        " lifecycle, created_at) VALUES ('faded', 'mem1', ?, ?, 'archived', ?)",
        (_PROFILE, "An old note nobody has needed for a year.",
         "2025-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO fact_retention (fact_id, profile_id, retention_score,"
        " lifecycle_zone) VALUES ('faded', ?, 0.10, 'archive')", (_PROFILE,),
    )

    # Two consolidator-authored rows: memory_id='' and a provenance ledger row.
    for i, text in enumerate((
        "Unfortunately, there is no information available about 'State' in "
        "the provided text.",
        "The Pro and SuperLocalMemory projects have made significant progress "
        "across several releases this year.",
    )):
        fid = f"summary-{i}"
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
            " importance, evidence_count, lifecycle, created_at) "
            "VALUES (?, '', ?, ?, 0.8, 450, 'active', ?)",
            (fid, _PROFILE, text, "2026-08-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO fact_consolidations (consolidation_id, profile_id,"
            " consolidated_fact_id, source_fact_ids, strategy, created_at) "
            "VALUES (?, ?, ?, ?, 'entity_cluster', ?)",
            (f"c{i}", _PROFILE, fid,
             json.dumps(["real-0", "real-1", "real-2"]),
             "2026-08-01T00:00:00+00:00"),
        )

    # A fact with an empty memory_id that is NOT a consolidation target. Both
    # halves of the predicate are required, so this must survive untouched.
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
        " lifecycle, created_at) VALUES ('stray', '', ?, ?, 'active', ?)",
        (_PROFILE, "A fact whose parent memory row went missing somehow.",
         "2026-07-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return {"genuine_wrongly_hidden": 3, "consolidator_rows": 2}


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    db = tmp_path / "memory.db"
    _poisoned_store(db)
    return db


def _open(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _one(conn: sqlite3.Connection, sql: str, *p: object) -> object:
    row = conn.execute(sql, p).fetchone()
    return row[0] if row else None


class TestItPreservesBeforeItWithholds:
    def test_every_withheld_summary_is_still_visible_somewhere(
        self, store: Path,
    ) -> None:
        """Withholding without preserving would delete the owner's view.

        These summaries are the only thing on the dashboard's summary tab. If
        the repair took them out of recall and did not put them anywhere, the
        owner's experience of the fix would be that their summaries vanished.
        """
        conn = _open(store)
        try:
            M043.apply(conn)
            orphaned = _one(conn, """
                SELECT COUNT(*) FROM atomic_facts af
                 WHERE af.quarantined = 1
                   AND NOT EXISTS (
                         SELECT 1 FROM consolidated_summaries cs
                          WHERE cs.profile_id = af.profile_id
                            AND cs.content = af.content)
            """)
            assert orphaned == 0, (
                f"{orphaned} summaries were withheld from recall without being "
                "preserved for display"
            )
        finally:
            conn.close()

    def test_source_count_comes_from_the_ledger_not_evidence_count(
        self, store: Path,
    ) -> None:
        """The fixture's rows carry evidence_count=450 and three real sources.

        evidence_count was incremented every time the old reinforce-or-insert
        path saw the same summary text again, so showing it as "memories
        summarised" would tell the owner 450 when the truth is 3.
        """
        conn = _open(store)
        try:
            M043.apply(conn)
            rows = conn.execute(
                "SELECT source_count, source_fact_ids FROM consolidated_summaries"
            ).fetchall()
            assert rows, "nothing was preserved"
            for row in rows:
                assert row["source_count"] == len(json.loads(row["source_fact_ids"]))
                assert row["source_count"] == 3, (
                    f"source_count is {row['source_count']}, not the 3 ids in "
                    "the provenance ledger"
                )
        finally:
            conn.close()


class TestThePredicatesAreExact:
    def test_both_halves_are_required(self, store: Path) -> None:
        """A dangling memory_id alone must not get a memory withheld.

        'stray' has memory_id='' but no consolidation record. Withholding on
        the empty id alone would hide a real memory whose parent row went
        missing -- a bug in a different subsystem punished as if it were junk.
        """
        conn = _open(store)
        try:
            M043.apply(conn)
            assert _one(
                conn, "SELECT quarantined FROM atomic_facts WHERE fact_id='stray'"
            ) == 0, "a non-consolidator row with an empty memory_id was withheld"
            assert _one(
                conn, "SELECT COUNT(*) FROM atomic_facts WHERE quarantined=1"
            ) == 2
        finally:
            conn.close()

    def test_a_genuinely_faded_memory_stays_faded(self, store: Path) -> None:
        """The restore is scoped by the retention score, not by 'was archived'.

        'faded' scores 0.10, which the retention maths maps to 'archive'. It is
        archived because it should be. Restoring it would turn a targeted repair
        into un-forgetting the whole store.
        """
        conn = _open(store)
        try:
            M043.apply(conn)
            assert _one(
                conn,
                "SELECT lifecycle_zone FROM fact_retention WHERE fact_id='faded'",
            ) == "archive"
            assert _one(
                conn, "SELECT lifecycle FROM atomic_facts WHERE fact_id='faded'"
            ) == "archived"
        finally:
            conn.close()

    def test_wrongly_hidden_memories_come_back(self, store: Path) -> None:
        """Score 1.0 with zone 'archive' is a contradiction, so it is repaired.

        The zone is RECOMPUTED from the score, not guessed: 1.0 maps to
        'active'. Guessing 'warm' would have demoted 528 facts on the author's
        real store that the maths already called active.
        """
        conn = _open(store)
        try:
            before = _one(conn, """
                SELECT COUNT(*) FROM fact_retention r JOIN atomic_facts af
                  ON af.fact_id = r.fact_id
                 WHERE r.lifecycle_zone='archive' AND r.retention_score > 0.8
                   AND af.memory_id <> ''
            """)
            assert before == 3, f"fixture is wrong: {before} wrongly-hidden rows"
            M043.apply(conn)
            for i in range(3):
                assert _one(
                    conn,
                    "SELECT lifecycle_zone FROM fact_retention WHERE fact_id=?",
                    f"real-{i}",
                ) == "active"
                assert _one(
                    conn, "SELECT lifecycle FROM atomic_facts WHERE fact_id=?",
                    f"real-{i}",
                ) == "active", "the legacy lifecycle mirror was left stale"
        finally:
            conn.close()


class TestItIsSafeToRunForever:
    def test_verify_is_false_before_and_true_after(self, store: Path) -> None:
        conn = _open(store)
        try:
            assert M043.verify(conn) is False
            M043.apply(conn)
            assert M043.verify(conn) is True
        finally:
            conn.close()

    def test_three_runs_change_nothing_after_the_first(self, store: Path) -> None:
        """It runs on every start. Drift across runs would be cumulative."""
        conn = _open(store)
        try:
            M043.apply(conn)
            def state() -> tuple:
                return (
                    _one(conn, "SELECT COUNT(*) FROM atomic_facts"),
                    _one(conn, "SELECT COUNT(*) FROM atomic_facts WHERE quarantined=1"),
                    _one(conn, "SELECT COUNT(*) FROM consolidated_summaries"),
                    _one(conn, "SELECT group_concat(lifecycle_zone) FROM "
                               "(SELECT lifecycle_zone FROM fact_retention "
                               " ORDER BY fact_id)"),
                )
            first = state()
            M043.apply(conn)
            M043.apply(conn)
            assert state() == first
        finally:
            conn.close()

    def test_it_survives_a_store_with_nothing_to_repair(self, tmp_path: Path) -> None:
        """A fresh install reaches this migration too, and must not see an error."""
        db = tmp_path / "fresh.db"
        conn = _open(db)
        try:
            create_all_tables(conn)
            conn.execute(
                "CREATE TABLE fact_consolidations (consolidation_id TEXT PRIMARY KEY,"
                " profile_id TEXT, consolidated_fact_id TEXT NOT NULL,"
                " source_fact_ids TEXT NOT NULL, strategy TEXT, created_at TEXT)"
            )
            M043.apply(conn)
            assert M043.verify(conn) is True
            assert _one(conn, "SELECT COUNT(*) FROM consolidated_summaries") == 0
        finally:
            conn.close()

    def test_repair_is_reachable_and_is_the_same_operation(self, store: Path) -> None:
        """The runner calls repair() when verify() fails on a completed row.

        That makes M043 a standing guard rather than a one-shot: if pollution
        ever reappears, the next start withholds it with nobody asking.
        """
        conn = _open(store)
        try:
            M043.repair(conn)
            assert M043.verify(conn) is True
        finally:
            conn.close()


class TestTheRepairIsRegisteredWhereItWillActuallyRun:
    def test_it_is_deferred_not_eager(self) -> None:
        """Eager runs before atomic_facts exists, so it would fail on install.

        Every existing migration that touches atomic_facts -- M011, M013, M015,
        M016 -- is deferred for this reason.
        """
        from superlocalmemory.storage.migration_runner import (
            DEFERRED_MIGRATIONS,
            MIGRATIONS,
        )
        eager = {m.name for m in MIGRATIONS}
        deferred = {m.name for m in DEFERRED_MIGRATIONS}
        assert M043.NAME in deferred
        assert M043.NAME not in eager

    def test_the_deferred_pass_snapshots_before_it_applies_anything(self) -> None:
        """The store must be recoverable, and by the backup API, not a copy.

        A file copy cannot snapshot a live WAL database -- it can capture a torn
        set of pages that looks fine until the day it is needed.
        """
        import inspect

        from superlocalmemory.storage import migration_runner

        src = inspect.getsource(migration_runner.apply_deferred)
        assert "_ensure_snapshot()" in src, (
            "apply_deferred does not snapshot, so a repair that mutates data "
            "would have no recoverable copy behind it"
        )
        assert "_pre_migration_backup" in src
        backup_src = inspect.getsource(migration_runner._pre_migration_backup)
        assert "copy2" not in backup_src, (
            "the snapshot uses shutil.copy2, which cannot produce a consistent "
            "copy of a live WAL database"
        )

    def test_it_is_in_the_module_registry_or_verify_never_runs(self) -> None:
        """Without a registry entry, verify()/repair() are never consulted.

        The migration would apply once and then never guard anything, which is
        the difference between a one-off fix and a standing repair.
        """
        from superlocalmemory.storage._migration_internals import _MODULES

        assert _MODULES.get(M043.NAME) is M043
