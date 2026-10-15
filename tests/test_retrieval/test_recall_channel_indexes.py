"""The migration must remove measured scans without changing ranked top-k."""

from benchmark.recall_channels import seed

from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage.database import DatabaseManager


def rankings(db):
    entity = EntityGraphChannel(db)
    temporal = TemporalChannel(db)
    bm25 = BM25Channel(db)
    return [
        entity.search("Alice", "default", 20),
        entity.score_candidates("Alice", [f"f{i:06}" for i in range(20)], "default"),
        temporal.search("Alice on 2025-03-15T00:00:00", "default", 20),
        bm25.search("Alice release", "default", 20),
    ]


def test_indexes_preserve_ranked_results_and_remove_scans(tmp_path):
    from superlocalmemory.storage.migrations import M056_recall_channel_indexes as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 200)
    before = rankings(db)
    conn = db._connect()
    try:
        assert not migration.verify(conn)
        migration.apply(conn)
        conn.commit()
        assert migration.verify(conn)
        migration.repair(conn)
        conn.commit()
    finally:
        conn.close()
    assert rankings(db) == before
    queries = [
        ("SELECT entity_id FROM canonical_entities WHERE canonical_name COLLATE NOCASE=?",
         ("alice",),
         "idx_recall_entity_name"),
        ("SELECT entity_id FROM entity_aliases WHERE alias COLLATE NOCASE=?", ("alice",),
         "idx_aliases_lookup"),
        ("SELECT fact_id FROM temporal_events WHERE entity_id=?", ("e0",),
         "idx_recall_temporal_entity"),
        ("SELECT source_id,target_id,edge_type FROM graph_edges WHERE profile_id=? "
         "AND edge_type='contradiction' AND (source_id IN (?) OR target_id IN (?))",
         ("default", "f000000", "f000001"), "idx_recall_contradictions"),
    ]
    for sql, params, name in queries:
        plan = " ".join(str(tuple(row)) for row in db.execute("EXPLAIN QUERY PLAN " + sql, params))
        assert name in plan
    db.close()


def test_new_migration_is_registered_in_both_runner_catalogues():
    from superlocalmemory.storage import _migration_catalogue, _migration_internals
    from superlocalmemory.storage.migrations import M056_recall_channel_indexes as migration

    assert migration.NAME in {m.name for m in _migration_catalogue.DEFERRED_MIGRATIONS}
    assert _migration_internals._MODULES[migration.NAME] is migration


def test_ambiguous_resolver_winners_are_unchanged(tmp_path):
    from superlocalmemory.encoding.entity_resolver import EntityResolver
    from superlocalmemory.storage.migrations import M056_recall_channel_indexes as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20)
    for eid, name, count in (("z", "Duplicate", 99), ("a", "DUPLICATE", 3)):
        db.execute("INSERT INTO canonical_entities(entity_id,canonical_name,fact_count) "
                   "VALUES (?,?,?)", (eid, name, count))
        db.execute("INSERT INTO entity_aliases(alias_id,entity_id,alias,source) VALUES (?,?,?,?)",
                   (eid, eid, "Ambiguous", "manual"))
    resolver = EntityResolver(db)
    before = (db.get_entity_by_name("duplicate", "default").entity_id,
              resolver._alias_lookup("ambiguous", "default"))
    with db.transaction():
        migration.apply(db._txn_state.conn)
    after = (db.get_entity_by_name("duplicate", "default").entity_id,
             resolver._alias_lookup("ambiguous", "default"))
    assert after == before
    db.close()
