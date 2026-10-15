# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The repair sequence the fork ran in production, pinned against the
upstream implementation that replaced it.

HISTORY. The fork shipped a score guard on 2026-09-25 (运维笔记 §19/§21):
the maintenance Langevin chain derived zones from position radius alone and
saturated positions archived healthy memories nightly. Upstream fixed the
same defect at the root in 4.1.21 (74cb08b8): every pass now REMEASURES the
radius as ``1 - R(t)`` (``core/langevin_tier.py``), so positions cannot
drift and the guard retired. Three weeks of production held archive at 0.

What this file still pins — the guarantees the repair must keep under the
upstream implementation:
  - a saturated legacy position cannot keep a used memory archived: the
    next pass remeasures the radius and the tier follows the Ebbinghaus
    retention the usage implies (the upstream pass reads
    access_count/age/importance only — it never consults
    ``fact_retention.retention_score``, so the score fixtures below are the
    fork-era authority, kept to prove the score can no longer hijack the
    tier);
  - M051 re-runnability (the fork's runbook relies on apply() twice);
  - the full production repair sequence (maintenance -> M051 -> M043 ->
    reseed) converges with zones held.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.schema import create_all_tables
from superlocalmemory.core.maintenance import run_maintenance

_PROFILE = "default"
_SATURATED = [0.35] * 8   # norm 0.9899 — the production signature (§19)


def _cfg() -> SLMConfig:
    cfg = SLMConfig()
    cfg.math.sheaf_at_encoding = False
    cfg.math.fisher_bayesian_update = False
    cfg.math.ebbinghaus_langevin_coupling_enabled = False
    return cfg


def _insert_fact(conn, fact_id, *, lifecycle="active", position=None,
                 age_days=60, access_count=0):
    conn.execute(
        "INSERT OR IGNORE INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', ?, 'source')", (_PROFILE,),
    )
    created = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
        " lifecycle, langevin_position, access_count, scope, created_at)"
        " VALUES (?, 'm1', ?, ?, ?, ?, ?, 'global', ?)",
        (fact_id, _PROFILE, f"content {fact_id}", lifecycle,
         json.dumps(position) if position is not None else None,
         access_count, created),
    )


def _insert_retention(conn, fact_id, *, zone, score):
    conn.execute(
        "INSERT INTO fact_retention (fact_id, profile_id, lifecycle_zone,"
        " retention_score) VALUES (?, ?, ?, ?)",
        (fact_id, _PROFILE, zone, score),
    )


def _zone(db, fact_id):
    rows = db.execute(
        "SELECT lifecycle_zone FROM fact_retention WHERE fact_id=?", (fact_id,))
    return str(dict(rows[0])["lifecycle_zone"]) if rows else None


def _radius(db, fact_id):
    rows = db.execute(
        "SELECT langevin_position FROM atomic_facts WHERE fact_id=?",
        (fact_id,),
    )
    raw = dict(rows[0])["langevin_position"]
    pos = json.loads(raw) if raw else None
    return sum(x * x for x in pos) ** 0.5 if pos else None


@pytest.fixture()
def store(tmp_path: Path) -> DatabaseManager:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    create_all_tables(conn)
    conn.commit()
    conn.close()
    return DatabaseManager(str(tmp_path / "memory.db"))


class TestSaturatedLegacyPositionCannotArchiveAUsedMemory:
    def test_remeasure_frees_the_tier_and_the_radius(self, store, tmp_path):
        """§19's production shape: saturated position on a USED memory.
        Under the upstream pass the radius is re-derived as 1 - R(t) from
        access_count/age, so the tier follows the usage and the radius
        leaves the boundary. (The score=0.9 retention row is the fork-era
        authority fixture: the upstream pass never reads it.)"""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="archived", position=_SATURATED,
                     access_count=8)
        _insert_retention(conn, "f1", zone="archive", score=0.9)
        conn.commit()
        conn.close()

        run_maintenance(store, _cfg(), _PROFILE)

        assert _zone(store, "f1") in {"active", "warm"}, (
            "a memory whose retention keeps it must not stay archived"
        )
        r = _radius(store, "f1")
        assert r is not None and r < 0.80, (
            f"the saturated radius must be re-measured below the archive "
            f"boundary, got {r}"
        )


class TestM051Rerun:
    def test_apply_twice_then_reseed_once(self, tmp_path):
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        create_all_tables(conn)
        _insert_fact(conn, "f1", lifecycle="archived", position=_SATURATED)
        _insert_fact(conn, "f2", lifecycle="archived", position=_SATURATED)
        conn.commit()

        m051.apply(conn)
        conn.commit()
        assert all(
            dict(r)["langevin_position"] is None
            for r in conn.execute(
                "SELECT langevin_position FROM atomic_facts")
        )
        m051.apply(conn)
        conn.commit()
        conn.close()

        db = DatabaseManager(str(path))
        counts1 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts1["langevin_backfilled"] == 2
        counts2 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts2["langevin_backfilled"] == 0
        db.close()


class TestProductionReplay:
    def test_saturated_store_repair_sequence(self, tmp_path):
        from superlocalmemory.storage.migrations import (
            M043_quarantine_display_summaries as m043,
        )
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
        create_all_tables(conn)
        _insert_fact(conn, "g1", lifecycle="active", position=_SATURATED)
        _insert_fact(conn, "g2", lifecycle="active", position=_SATURATED)
        _insert_retention(conn, "g1", zone="active", score=1.0)
        _insert_retention(conn, "g2", zone="active", score=1.0)
        _insert_fact(conn, "p1", lifecycle="archived", position=_SATURATED)
        _insert_retention(conn, "p1", zone="archive", score=1.0)
        conn.commit()

        db = DatabaseManager(str(path))
        run_maintenance(db, _cfg(), _PROFILE)
        db.close()

        conn = sqlite3.connect(str(path))
        m051.apply(conn)
        conn.commit()
        m043.apply(conn)
        conn.close()

        db = DatabaseManager(str(path))
        # Upstream semantics: tier follows 1 - R(t) from access_count/age,
        # not the score row. A 60-day, 0-access fixture measures R(t)≈0.77
        # (v4.2.6 math) ⇒ warm; the pin is "no longer archived", and under
        # the guard-era implementation this same assertion read "active".
        assert _zone(db, "p1") in {"active", "warm"}
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 3
        for fid in ("g1", "g2", "p1"):
            assert _zone(db, fid) in {"active", "warm"}
            assert _radius(db, fid) is not None
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 0
        db.close()
