# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""BRN-06: consolidation ``dry_run=True`` previews and writes nothing.

The audit's fixture: an old, never-accessed fact at confidence 0.8. A dry run
reported ``decayed=1`` AND persisted 0.799. Every table of both databases is
compared, not just the returned counts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.test_storage._upgrade_store import current_store


def _dump(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [line for line in conn.iterdump()
                if not line.startswith("INSERT INTO \"sqlite_sequence\"")]


def test_dry_run_counts_and_changes_no_row(tmp_path: Path) -> None:
    from superlocalmemory.learning.consolidation_worker import ConsolidationWorker

    learning, memory = current_store(tmp_path / "store")
    with sqlite3.connect(memory) as conn:
        conn.execute("INSERT OR IGNORE INTO profiles (profile_id, name) "
                     "VALUES ('default', 'default')")
        conn.execute("INSERT INTO memories (memory_id, profile_id, content) "
                     "VALUES ('m1', 'default', 'old')")
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
            " confidence, access_count, created_at) VALUES ('f1', 'm1', "
            "'default', 'an old unused fact', 0.8, 0, '2025-01-01 00:00:00')")
    before = (_dump(memory), _dump(learning))
    stats = ConsolidationWorker(memory, learning).run("default", dry_run=True)
    assert stats["decayed"] == 1  # the preview still says what WOULD change
    assert (_dump(memory), _dump(learning)) == before
    with sqlite3.connect(memory) as conn:
        assert conn.execute("SELECT confidence FROM atomic_facts "
                            "WHERE fact_id='f1'").fetchone() == (0.8,)

    ConsolidationWorker(memory, learning).run("default", dry_run=False)
    with sqlite3.connect(memory) as conn:
        assert conn.execute("SELECT confidence FROM atomic_facts "
                            "WHERE fact_id='f1'").fetchone()[0] < 0.8
