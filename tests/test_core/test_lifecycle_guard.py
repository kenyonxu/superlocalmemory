# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The Langevin radius may propose, the retention score decides.

WHAT PRODUCTION SHOWED (运维笔记 §19). The maintenance Langevin chain writes
``lifecycle`` from ``radius -> weight -> zone`` without ever reading the
retention score. Positions drift outward nightly and saturate at 0.99; once
saturated, every fact's zone flips to archived on the next pass regardless of
a perfect score. Measured 2026-09-24: 3,306 archived rows whose zone agreed
with the radius 100% of the time and whose retention score still read 1.0.

THE GUARD. A radius-driven write may warm a fact freely, but it may never
cool one past the zone the score authority (``fact_retention.lifecycle_zone``,
written by the decay cycle's ``batch_upsert_retention``) last recorded.
Legitimate cooling still happens — through the decay cycle, which runs in the
same daemon tick (scheduler_interval_minutes=30), at most one tick later.

This file pins the guard (unit) and the production repair sequence
(integration): M051 re-run clears saturated positions, M043 restores the
rows the radius convicted, and the seeder never overwrites a position.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.lifecycle_state import set_fact_lifecycle_zone
from superlocalmemory.core.maintenance import (
    _guard_zone_updates,
    _is_colder,
    _persist_lifecycle,
    run_maintenance,
)
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.schema import create_all_tables

_PROFILE = "default"


class TestIsColder:
    @pytest.mark.parametrize(
        "proposed,current,expected",
        [
            ("archived", "active", True),
            ("archive", "warm", True),        # retention spelling
            ("forgotten", "archived", True),
            ("cold", "warm", True),
            ("warm", "cold", False),          # warming is never colder
            ("active", "archived", False),
            ("warm", "warm", False),          # equal is not colder
            ("archived", "archive", False),   # spellings share a rank
            ("nonsense", "active", False),    # unreadable input never blocks
            ("archived", "nonsense", False),
            (None, "active", False),
            ("archived", None, False),
        ],
    )
    def test_direction(self, proposed, current, expected) -> None:
        assert _is_colder(proposed, current) is expected


class TestGuardZoneUpdates:
    def test_colder_is_refused_but_the_position_survives(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "archived", [0.35] * 8)], {"f1": "active"},
        )
        assert refused == 1
        assert guarded == [("f1", None, [0.35] * 8)]

    def test_warmer_is_written(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "active", [0.05] * 8)], {"f1": "cold"},
        )
        assert refused == 0
        assert guarded == [("f1", "active", [0.05] * 8)]

    def test_same_zone_is_a_skipped_noop(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "warm", None)], {"f1": "warm"},
        )
        assert refused == 0
        assert guarded == [("f1", None, None)]

    def test_no_authority_record_writes_proposed(self) -> None:
        guarded, refused = _guard_zone_updates([("f1", "cold", None)], {})
        assert refused == 0
        assert guarded == [("f1", "cold", None)]

    def test_spelling_variants_share_a_rank(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "archived", None)], {"f1": "archive"},
        )
        assert refused == 0
        assert guarded == [("f1", None, None)]


class TestEveryRadiusWriteSiteIsGuarded:
    def test_all_radius_sites_pass_through_the_guard(self) -> None:
        """AST, not grep: a comment mentioning the pattern is not the pattern.

        Four ``_persist_lifecycle`` calls live in run_maintenance. The three
        radius-driven ones (seed, batch step, Fisher re-step) must persist the
        GUARDED updates. The ELC one is deliberately raw: its zone comes from
        the retention score, which IS the authority, and it passes an inline
        list whose position element is None.
        """
        import ast
        import inspect

        from superlocalmemory.core import maintenance

        tree = ast.parse(inspect.getsource(maintenance.run_maintenance))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_persist_lifecycle"
        ]
        assert len(calls) == 4, f"expected 4 write sites, found {len(calls)}"
        for call in calls:
            updates_arg = call.args[2]
            if isinstance(updates_arg, ast.Name):
                assert updates_arg.id == "guarded", (
                    f"line {call.lineno}: radius write site must persist "
                    "the guarded updates"
                )
                continue
            # The one deliberately unguarded site is ELC: its zone comes
            # from the retention score, which IS the authority. Recognise
            # it by its inline single-row list whose position is None.
            is_elc = (
                isinstance(updates_arg, ast.List)
                and len(updates_arg.elts) == 1
                and isinstance(updates_arg.elts[0], ast.Tuple)
                and len(updates_arg.elts[0].elts) == 3
                and isinstance(updates_arg.elts[0].elts[2], ast.Constant)
                and updates_arg.elts[0].elts[2].value is None
            )
            assert is_elc, (
                f"line {call.lineno}: unguarded _persist_lifecycle outside "
                "the ELC site — radius-driven writes must go through "
                "_guard_zone_updates first"
            )


# ---------------------------------------------------------------------------
# Integration fixtures: a real store, not a double
# ---------------------------------------------------------------------------

_SATURATED = [0.35] * 8   # norm 0.9899 — the production signature (§19)
_CENTER = [0.03] * 8      # norm 0.0849 — deep in ACTIVE with step-noise margin


def _cfg() -> SLMConfig:
    cfg = SLMConfig()
    cfg.math.sheaf_at_encoding = False
    cfg.math.fisher_bayesian_update = False
    cfg.math.ebbinghaus_langevin_coupling_enabled = False
    # langevin_persist_positions stays True — the thing under test.
    return cfg


def _insert_fact(
    conn: sqlite3.Connection,
    fact_id: str,
    *,
    lifecycle: str = "active",
    position: list[float] | None = None,
    age_days: int = 60,
    access_count: int = 0,
) -> None:
    from datetime import UTC, datetime, timedelta

    conn.execute(
        "INSERT OR IGNORE INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', ?, 'source')",
        (_PROFILE,),
    )
    created = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
        " lifecycle, langevin_position, access_count, scope, created_at)"
        " VALUES (?, 'm1', ?, ?, ?, ?, ?, 'global', ?)",
        (
            fact_id, _PROFILE, f"content {fact_id}", lifecycle,
            json.dumps(position) if position is not None else None,
            access_count, created,
        ),
    )


def _insert_retention(
    conn: sqlite3.Connection,
    fact_id: str,
    *,
    zone: str,
    score: float,
) -> None:
    conn.execute(
        "INSERT INTO fact_retention (fact_id, profile_id, lifecycle_zone,"
        " retention_score) VALUES (?, ?, ?, ?)",
        (fact_id, _PROFILE, zone, score),
    )


def _zone(db: DatabaseManager, fact_id: str) -> str | None:
    rows = db.execute(
        "SELECT lifecycle_zone FROM fact_retention WHERE fact_id=?",
        (fact_id,),
    )
    return str(dict(rows[0])["lifecycle_zone"]) if rows else None


def _lifecycle(db: DatabaseManager, fact_id: str) -> str:
    rows = db.execute(
        "SELECT lifecycle FROM atomic_facts WHERE fact_id=?", (fact_id,),
    )
    return str(dict(rows[0])["lifecycle"])


def _position(db: DatabaseManager, fact_id: str):
    rows = db.execute(
        "SELECT langevin_position FROM atomic_facts WHERE fact_id=?",
        (fact_id,),
    )
    raw = dict(rows[0])["langevin_position"]
    return json.loads(raw) if raw else None


@pytest.fixture()
def store(tmp_path: Path) -> DatabaseManager:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    create_all_tables(conn)
    conn.commit()
    conn.close()
    return DatabaseManager(str(tmp_path / "memory.db"))


class TestGuardAgainstLiveMaintenance:
    """spec 测试 2/3/5/6: the radius proposes, the score disposes."""

    def test_radius_cannot_archive_a_fact_the_score_keeps(
        self, store, tmp_path,
    ) -> None:
        """The 9/24 production failure, replayed: saturated position, score
        0.9, zone active. One maintenance pass must not move the zone."""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="active", position=_SATURATED)
        _insert_retention(conn, "f1", zone="active", score=0.9)
        conn.commit()
        conn.close()

        counts = run_maintenance(store, _cfg(), _PROFILE)

        assert _zone(store, "f1") == "active"
        assert _lifecycle(store, "f1") == "active"
        assert counts["langevin_guard_refused"] >= 1
        # The radius domain is intact: the position was stepped and
        # persisted, so retrieval weighting still reads it. (spec 测试 5)
        pos = _position(store, "f1")
        assert pos is not None and len(pos) == 8

    def test_score_driven_cooling_arrives_first_then_radius_agrees(
        self, store, tmp_path,
    ) -> None:
        """spec 测试 3/6: legitimate cooling flows through the authority.
        Once the decay cycle's write lands, the radius proposal is the same
        zone and sails through without a refusal."""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="warm", position=_SATURATED)
        _insert_retention(conn, "f1", zone="warm", score=0.42)
        conn.commit()
        conn.close()
        counts = run_maintenance(store, _cfg(), _PROFILE)
        # Radius proposed archived against a warm authority: refused.
        assert _zone(store, "f1") == "warm"
        assert counts["langevin_guard_refused"] >= 1

        # The score recomputes down (what batch_upsert_retention writes).
        set_fact_lifecycle_zone(store, ["f1"], "archive", profile_id=_PROFILE)
        assert _zone(store, "f1") == "archive"

        counts = run_maintenance(store, _cfg(), _PROFILE)
        assert _zone(store, "f1") == "archive"
        assert _lifecycle(store, "f1") == "archived"
        # Equal rank is a skipped no-op, not a refusal.
        assert counts["langevin_guard_refused"] == 0

    def test_warming_is_free(self, store, tmp_path) -> None:
        """A fact the authority cooled may be warmed by the radius — the
        decay cycle will re-cool it next tick if the score disagrees."""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="cold", position=_CENTER,
                     access_count=25)
        _insert_retention(conn, "f1", zone="cold", score=0.3)
        conn.commit()
        conn.close()
        run_maintenance(store, _cfg(), _PROFILE)
        # _CENTER steps stay well inside the ACTIVE band (< 0.20).
        assert _zone(store, "f1") == "active"


class TestM051Rerun:
    """spec 测试 7 + ④ 防线: clear is rerunnable; the reseed happens once."""

    def test_apply_twice_then_reseed_once(self, tmp_path) -> None:
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row  # the dict(r) reads name columns
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
        # Rerunnable: the second clear has nothing to do and breaks nothing.
        m051.apply(conn)
        conn.commit()
        conn.close()

        db = DatabaseManager(str(path))
        counts1 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts1["langevin_backfilled"] == 2
        assert _position(db, "f1") is not None
        # ④: the seeder never overwrites. Pass two reseeds nothing.
        counts2 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts2["langevin_backfilled"] == 0
        assert _position(db, "f1") is not None


class TestProductionReplay:
    """spec 测试 8: the 9/24 store shape, repaired in the spec's order."""

    def test_saturated_store_repair_sequence(self, tmp_path) -> None:
        from superlocalmemory.storage.migrations import (
            M043_quarantine_display_summaries as m043,
        )
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
        create_all_tables(conn)
        # Two healthy rows the radius convicted (score 1.0, zone active,
        # saturated position) — the 9/23 surgery victims.
        _insert_fact(conn, "g1", lifecycle="active", position=_SATURATED)
        _insert_fact(conn, "g2", lifecycle="active", position=_SATURATED)
        _insert_retention(conn, "g1", zone="active", score=1.0)
        _insert_retention(conn, "g2", zone="active", score=1.0)
        # One row already flipped: archived by the radius, score still 1.0.
        _insert_fact(conn, "p1", lifecycle="archived", position=_SATURATED)
        _insert_retention(conn, "p1", zone="archive", score=1.0)
        conn.commit()

        # ① With the guard in, maintenance holds the line even against a
        # saturated position: nothing cools.
        db = DatabaseManager(str(path))
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert _zone(db, "g1") == "active"
        assert _zone(db, "p1") == "archive"  # equal rank: allowed, no-op
        assert counts["langevin_guard_refused"] >= 2
        db.close()

        # ② M051 re-run: positions cleared so the seed can re-measure.
        conn = sqlite3.connect(str(path))
        m051.apply(conn)
        conn.commit()

        # ③ M043 restore: the score says p1 was wrongly hidden. Its apply()
        # carries its own BEGIN IMMEDIATE/COMMIT, so M051's transaction must
        # already be committed (above) — the same order the production
        # runbook uses.
        m043.apply(conn)
        conn.close()

        db = DatabaseManager(str(path))
        assert _zone(db, "p1") == "active"        # authority restored
        assert _lifecycle(db, "p1") == "active"   # mirror followed

        # ④ Reseed re-measures; old unaccessed facts seed at a decayed
        # radius, and the guard still refuses to let that radius cool what
        # the score keeps. Zones hold; positions exist; nothing reseeds
        # twice.
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 3
        assert _zone(db, "g1") == "active"
        assert _zone(db, "g2") == "active"
        assert _zone(db, "p1") == "active"
        assert _position(db, "p1") is not None
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 0
        assert _zone(db, "g1") == "active"
        db.close()
