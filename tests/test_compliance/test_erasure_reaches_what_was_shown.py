# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""Article 17 has to reach the record of which memories a person was shown.

``LearningDatabase.reset`` is the learning half of ``forget_profile``. Its table
list was hardcoded to four names — ``learning_signals``, ``learning_features``,
``learning_model_state``, ``engagement_metrics`` — and the bandit tables were
not among them. ``compliance/`` never mentions them either.

That was survivable while ``bandit_plays`` held only (profile, stratum, arm,
timestamps). It stopped being survivable when ``shown_fact_ids`` was added,
because that column is a list of the identifiers of memories actually shown to
a person. Reproduced before fixing: after erasing a profile, its
``learning_signals`` rows were gone and a row reading
``["alice-private-memory-1", "alice-private-memory-2"]`` was still there.

``bandit_arms`` is a derived behavioural profile for the same person and goes
with it.

WHY THE RETENTION SWEEP IS NOT A SUBSTITUTE: it deletes only SETTLED plays past
the horizon. An unsettled play is never swept, so the row would have persisted
indefinitely.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.learning.database import LearningDatabase
from superlocalmemory.storage.migrations import M005_bandit_tables as _M005
from superlocalmemory.storage.migrations import (
    M044_play_carries_its_own_evidence as _M044,
)

_SHOWN = '["alice-private-memory-1", "alice-private-memory-2"]'


@pytest.fixture()
def learning_db(tmp_path: Path) -> Path:
    db = tmp_path / "learning.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_M005.DDL)
    _M044.apply(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS learning_signals (profile_id TEXT,"
        " query TEXT, fact_id TEXT, signal_type TEXT, value REAL,"
        " created_at TEXT)"
    )
    for who in ("alice", "bob"):
        conn.execute(
            "INSERT INTO learning_signals VALUES (?,'q','f','legacy_feedback',"
            " 1.0,'2026-01-01')", (who,),
        )
        conn.execute(
            "INSERT INTO bandit_plays (profile_id, query_id, stratum, arm_id,"
            " played_at, shown_fact_ids) VALUES (?,'q1','s','arm-1',"
            " '2026-01-01',?)", (who, _SHOWN),
        )
        conn.execute(
            "INSERT INTO bandit_arms (profile_id, stratum, arm_id, alpha, beta,"
            " plays, last_played_at) VALUES (?,'s','arm-1',7.0,2.0,9,"
            " '2026-01-01')", (who,),
        )
    conn.commit()
    conn.close()
    return db


def _count(db: Path, table: str, profile: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE profile_id = ?", (profile,),
        ).fetchone()[0]
    finally:
        conn.close()


class TestErasingAProfileReachesTheBanditTables:
    def test_the_memories_shown_to_that_person_are_gone(
        self, learning_db: Path,
    ) -> None:
        LearningDatabase(learning_db).reset("alice")
        assert _count(learning_db, "bandit_plays", "alice") == 0, (
            "the list of memories shown to this person survived erasure"
        )

    def test_their_learned_strategy_profile_is_gone(
        self, learning_db: Path,
    ) -> None:
        LearningDatabase(learning_db).reset("alice")
        assert _count(learning_db, "bandit_arms", "alice") == 0

    def test_the_signals_that_already_worked_still_work(
        self, learning_db: Path,
    ) -> None:
        LearningDatabase(learning_db).reset("alice")
        assert _count(learning_db, "learning_signals", "alice") == 0

    def test_nobody_elses_data_is_touched(self, learning_db: Path) -> None:
        """A scoped erasure that takes a second profile with it is worse."""
        LearningDatabase(learning_db).reset("alice")
        for table in ("learning_signals", "bandit_plays", "bandit_arms"):
            assert _count(learning_db, table, "bob") == 1, (
                f"erasing alice emptied bob's {table}"
            )

    def test_a_full_reset_clears_everyone(self, learning_db: Path) -> None:
        LearningDatabase(learning_db).reset()
        for table in ("learning_signals", "bandit_plays", "bandit_arms"):
            for who in ("alice", "bob"):
                assert _count(learning_db, table, who) == 0, (table, who)


class TestItDegradesRatherThanFailingHalfway:
    def test_a_store_without_the_bandit_tables_still_erases_the_rest(
        self, tmp_path: Path,
    ) -> None:
        """An Article 17 request that aborts midway is the worst outcome.

        The table list now includes names that a store predating M005 does not
        have. Erasure must skip those and finish, not raise and leave a profile
        half-deleted.
        """
        db = tmp_path / "old.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE learning_signals (profile_id TEXT, query TEXT,"
            " fact_id TEXT, signal_type TEXT, value REAL, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO learning_signals VALUES ('alice','q','f',"
            "'legacy_feedback',1.0,'2026-01-01')"
        )
        conn.commit()
        conn.close()

        LearningDatabase(db).reset("alice")      # must not raise
        assert _count(db, "learning_signals", "alice") == 0


class TestTheInProcessCounterIsClearedToo:
    def test_erasure_drops_the_profiles_recent_winners(
        self, learning_db: Path,
    ) -> None:
        """Ephemeral, but it must not outlive the erasure request.

        ``RECENT_TOPS`` holds which memories recently ranked first, per profile.
        It dies with the process, which is not the same as being erased on
        request.
        """
        from superlocalmemory.learning.pcos import RECENT_TOPS

        RECENT_TOPS.record_top("alice", "alice-private-memory-1")
        RECENT_TOPS.record_top("bob", "bobs-memory")
        assert RECENT_TOPS.tops("alice", "alice-private-memory-1") == 1

        LearningDatabase(learning_db).reset("alice")

        assert RECENT_TOPS.tops("alice", "alice-private-memory-1") == 0, (
            "the profile's recent winners are still held in this process"
        )
        assert RECENT_TOPS.tops("bob", "bobs-memory") == 1, (
            "erasing alice cleared bob's counter too"
        )
        RECENT_TOPS.forget("bob")
