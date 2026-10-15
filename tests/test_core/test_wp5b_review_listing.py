"""Accepted candidates leave review; faithful dates and speaker rewrites never enter."""

from superlocalmemory.cli.fidelity_cmd import _connect_read_only, _release, _to_review
from tests.test_core.test_source_fidelity_guard import DERIVED, _db, _ingest


def test_released_candidate_is_not_offered_again(tmp_path, monkeypatch):
    db = _db(tmp_path / "memory.db")
    _ingest(db, DERIVED)
    monkeypatch.setattr("superlocalmemory.cli.daemon.owned_daemon_process_alive", lambda: False)
    assert _release(str(db.db_path), "f-date")[0]
    with _connect_read_only(str(db.db_path)) as conn:
        assert _to_review(conn, "default", 50) == (0, [])


def test_listing_uses_source_actor_and_observation_date(tmp_path):
    db = _db(tmp_path / "memory.db")
    db.execute("INSERT INTO memories (memory_id,profile_id,content,speaker,role) "
               "VALUES ('m','default','I met Priya yesterday.','Alex','user')")
    db.execute("INSERT INTO atomic_facts (fact_id,memory_id,profile_id,content,observation_date) "
               "VALUES ('f','m','default','Alex met Priya on 2026-03-13.','2026-03-14')")
    with _connect_read_only(str(db.db_path)) as conn:
        assert _to_review(conn, "default", 50) == (0, [])


def test_truncated_measurement_is_offered_for_review(tmp_path):
    db = _db(tmp_path / "memory.db")
    _ingest(db, {"f-partial": "The Kestrel checkpoint was recalled at rank 1 in 2004."})
    db.execute("UPDATE atomic_facts SET quarantined=0 WHERE fact_id='f-partial'")
    with _connect_read_only(str(db.db_path)) as conn:
        total, rows = _to_review(conn, "default", 50)
    assert total == 1 and "number_became_date" in rows[0]["reasons"]
