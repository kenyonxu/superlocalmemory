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
