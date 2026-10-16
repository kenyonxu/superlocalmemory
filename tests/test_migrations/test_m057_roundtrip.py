# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The temporal counter survives a real downgrade, legacy SQL writes and repair."""

import sqlite3

from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage import _schema_version as sv
from superlocalmemory.storage import migration_runner as mr
from superlocalmemory.storage._downgrade import prepare_downgrade
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration
from tests.test_storage._upgrade_store import add_memory, current_store


def test_downgrade_legacy_write_reupgrade_and_repair(tmp_path):
    learning, memory = current_store(tmp_path)
    add_memory(memory, "m1", ["Alice before downgrade"], fact_ids=["f1"])
    db = DatabaseManager(memory)
    try:
        channel = TemporalChannel(db)
        before = channel._load_events("default", near_date="2025-03-15")
        version = db.execute("SELECT version FROM temporal_retrieval_version")[0][0]
        report = prepare_downgrade(target_schema=51, requested_by="test", data_root=tmp_path,
                                   memory_db=memory, learning_db=learning)
        assert report.prepared and sv.read_schema_version(memory) == 51
        # A separate legacy writer uses the 4.1.18 SQL contract, with no M057
        # API or explicit invalidation. The persisted triggers do the work.
        with sqlite3.connect(memory) as old:
            old.execute("INSERT INTO atomic_facts(fact_id,memory_id,profile_id,content) "
                        "VALUES ('old-f','m1','default','Alice from older build')")
            old.execute("INSERT OR REPLACE INTO temporal_events(event_id,profile_id,entity_id,"
                        "fact_id,referenced_date) VALUES ('old-t','default','e0','old-f',"
                        "'2025-03-15')")
        assert db.execute("SELECT version FROM temporal_retrieval_version")[0][0] > version
        for run in (mr.apply_all, mr.apply_deferred):
            assert run(learning, memory)["failed"] == []
        after = channel._load_events("default", near_date="2025-03-15")
        assert after != before and any(row["fact_id"] == "old-f" for row in after)
        assert after == TemporalChannel(db)._load_events("default", near_date="2025-03-15")
        with sqlite3.connect(memory) as conn:
            version = conn.execute("SELECT version FROM temporal_retrieval_version").fetchone()[0]
            conn.execute("DROP TRIGGER trg_temporal_slice_update")
            conn.execute("UPDATE temporal_retrieval_version SET uncacheable=99")
            assert not migration.verify(conn)
            migration.repair(conn)
            assert migration.verify(conn)
            repaired = conn.execute("SELECT version,uncacheable FROM temporal_retrieval_version")
            new_version, count = repaired.fetchone()
            assert new_version > version and count == 0
    finally:
        db.close()
