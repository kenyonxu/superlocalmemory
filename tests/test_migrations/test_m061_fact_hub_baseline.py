# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""M061: one additive table and an erase trigger; idempotent and repairable."""

from __future__ import annotations

import sqlite3

from superlocalmemory.storage._migration_internals import _MODULES
from superlocalmemory.storage.migration_runner import DEFERRED_MIGRATIONS
from superlocalmemory.storage.migrations import M061_fact_hub_baseline as M061


def _conn(with_facts: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    if with_facts:
        conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY)")
    return conn


def test_registered_deferred_with_a_downgrade_floor() -> None:
    assert _MODULES[M061.NAME] is M061
    assert M061.NAME in {m.name for m in DEFERRED_MIGRATIONS}
    assert M061.DOWNGRADE_FLOOR == 51 and M061.DB_TARGET == "memory"


def test_apply_twice_verify_and_repair() -> None:
    conn = _conn()
    assert not M061.verify(conn)
    M061.apply(conn)
    M061.apply(conn)
    assert M061.verify(conn)
    conn.execute("DROP TRIGGER trg_hub_baseline_fact_erased")
    assert not M061.verify(conn)
    M061.repair(conn)
    assert M061.verify(conn)


def test_erasing_a_fact_erases_its_baseline() -> None:
    conn = _conn()
    M061.apply(conn)
    conn.execute("INSERT INTO atomic_facts VALUES ('f1'), ('f2')")
    conn.executemany(
        "INSERT INTO fact_hub_baseline VALUES (?, 'default', 's', 'v', 0.1, 0.2, 0)",
        [("f1",), ("f2",)])
    conn.execute("DELETE FROM atomic_facts WHERE fact_id = 'f1'")
    assert [r[0] for r in conn.execute("SELECT fact_id FROM fact_hub_baseline")] == ["f2"]


def test_a_store_without_facts_gets_the_table_only() -> None:
    conn = _conn(with_facts=False)
    M061.apply(conn)
    assert M061.verify(conn)


def test_a_re_embedded_fact_loses_its_stale_baseline() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, embedding BLOB, "
                 "content TEXT)")
    M061.apply(conn)
    assert M061.verify(conn)
    conn.execute("INSERT INTO atomic_facts VALUES ('f1', x'00', 'a'), ('f2', x'00', 'b')")
    conn.executemany(
        "INSERT INTO fact_hub_baseline VALUES (?, 'default', 's', 'v', 0.1, 0.2, 0)",
        [("f1",), ("f2",)])
    conn.execute("UPDATE atomic_facts SET content = 'changed' WHERE fact_id = 'f2'")
    conn.execute("UPDATE atomic_facts SET embedding = x'01' WHERE fact_id = 'f1'")
    assert [r[0] for r in conn.execute("SELECT fact_id FROM fact_hub_baseline")] == ["f2"]
    conn.execute("DROP TRIGGER trg_hub_baseline_fact_reembedded")
    assert not M061.verify(conn)
    M061.repair(conn)
    assert M061.verify(conn)
