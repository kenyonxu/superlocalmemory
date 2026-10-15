"""Temporal slices reuse exact rows only while their write version is current."""

import sqlite3

from benchmark.recall_channels import seed

from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage.database import DatabaseManager


def test_temporal_slice_is_reused_but_dates_and_scope_writes_invalidate(tmp_path):
    from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 100)
    with db.transaction():
        migration.apply(db._txn_state.conn)
    channel = TemporalChannel(db)
    calls = []
    execute = db.execute

    def trace(sql, params=()):
        if "FROM temporal_events AS te" in sql:
            calls.append(sql)
        return execute(sql, params)

    db.execute = trace
    first = channel._load_events("default", near_date="2025-03-15")
    first[0]["fact_id"] = "caller mutation"
    second = channel._load_events("default", near_date="2025-03-15")
    assert len(calls) == 1
    assert second[0]["fact_id"] != "caller mutation"
    db.update_fact("f000000", {"access_count": 3})
    assert channel._load_events("default", near_date="2025-03-15") == second
    assert len(calls) == 1, "access accounting must not invalidate a temporal corpus"
    db.execute("UPDATE temporal_events SET referenced_date='2025-03-15' WHERE event_id='t0'")
    third = channel._load_events("default", near_date="2025-03-15")
    assert third != second and len(calls) == 2
    db.execute("UPDATE atomic_facts SET profile_id='team' WHERE fact_id='f000000'")
    fourth = channel._load_events("default", near_date="2025-03-15")
    assert all(row["fact_id"] != "f000000" for row in fourth)
    assert len(calls) == 3
    db.close()


def test_non_iso_dates_and_open_transactions_bypass_cache(tmp_path):
    from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20)
    db.execute("UPDATE temporal_events SET referenced_date='now' WHERE event_id='t0'")
    with db.transaction():
        migration.apply(db._txn_state.conn)
    channel = TemporalChannel(db)
    before = channel._load_events("default", near_date="2025-03-15")
    db.execute("UPDATE temporal_events SET referenced_date='subsec' WHERE event_id='t0'")
    assert channel._load_events("default", near_date="2025-03-15") != before
    db.execute("UPDATE temporal_events SET referenced_date='2025-01-01' WHERE event_id='t0'")
    baseline = channel._load_events("default", near_date="2025-03-15")
    try:
        with db.transaction():
            db.execute("UPDATE temporal_events SET referenced_date='2030-01-01' "
                       "WHERE event_id='t0'")
            assert channel._load_events("default", near_date="2025-03-15") != baseline
            raise RuntimeError("rollback")
    except RuntimeError:
        pass
    assert channel._load_events("default", near_date="2025-03-15") == baseline
    db.close()


def test_cache_is_bounded_and_preserves_same_fact_event_ties(tmp_path):
    from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20)
    for event, end in (("duplicate-a", "2026-01-01"), ("duplicate-b", "2027-01-01")):
        db.execute("INSERT INTO temporal_events(event_id,entity_id,fact_id,referenced_date,"
                   "interval_end) VALUES (?,'e0','f000000','2025-01-01',?)", (event, end))
    channel = TemporalChannel(db)
    before = channel.search("Alice on 2025-03-15T00:00:00", "default")
    with db.transaction():
        migration.apply(db._txn_state.conn)
    assert channel.search("Alice on 2025-03-15T00:00:00", "default") == before
    assert channel.search("Alice on 2025-03-15T00:00:00", "default") == before
    for day in range(1, 20):
        channel._load_events("default", near_date=f"2025-03-{day:02}")
    assert len(channel._slice_cache._rows) == 8
    db.execute("DELETE FROM temporal_events WHERE event_id='t0'")
    channel._load_events("default", near_date="2025-03-01")
    assert len(channel._slice_cache._rows) == 1
    db.close()


def test_repair_recounts_dynamic_rows_after_a_lost_trigger(tmp_path):
    from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20)
    with db.transaction():
        migration.apply(db._txn_state.conn)
        db.execute("DROP TRIGGER trg_temporal_slice_update")
        db.execute("UPDATE temporal_events SET referenced_date='now' WHERE event_id='t0'")
        assert not migration.verify(db._txn_state.conn)
        migration.repair(db._txn_state.conn)
        assert migration.verify(db._txn_state.conn)
    row = dict(db.execute("SELECT uncacheable FROM temporal_retrieval_version")[0])
    assert row["uncacheable"] == 1
    db.close()


def test_product_event_replacement_does_not_leave_an_unsafe_counter(tmp_path):
    from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration
    from superlocalmemory.storage.models import TemporalEvent

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20)
    with db.transaction():
        migration.apply(db._txn_state.conn)
    for date, expected in (("now", 1), ("now", 1), ("2025-03-15", 0)):
        db.store_temporal_event(TemporalEvent(event_id="t0", entity_id="e0", fact_id="f000000",
                                            referenced_date=date))
        row = dict(db.execute("SELECT uncacheable FROM temporal_retrieval_version")[0])
        assert row["uncacheable"] == expected
    db.close()


def test_fact_id_rename_invalidates_an_already_cached_join(tmp_path):
    from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    try:
        seed(db, 20)
        with db.transaction():
            migration.apply(db._txn_state.conn)
        channel = TemporalChannel(db)
        before = channel._load_events("default", near_date="2025-03-15")
        assert any(row["fact_id"] == "f000000" for row in before)
        version = db.execute("SELECT version FROM temporal_retrieval_version")[0][0]
        # Legacy / external SQLite writer, with foreign key enforcement off.
        with sqlite3.connect(db.db_path) as writer:
            writer.execute("UPDATE atomic_facts SET fact_id='renamed' WHERE fact_id='f000000'")
        assert db.execute("SELECT version FROM temporal_retrieval_version")[0][0] > version
        after = channel._load_events("default", near_date="2025-03-15")
        assert after != before and all(row["fact_id"] != "f000000" for row in after)
        assert after == TemporalChannel(db)._load_events("default", near_date="2025-03-15")
    finally:
        db.close()
