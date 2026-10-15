"""Review candidates commit quarantined, before any enrichment can consume them."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from superlocalmemory.core.source_fact_set import source_fact_set
from superlocalmemory.storage.models import AtomicFact, MemoryRecord
from tests.test_core.test_source_fidelity_guard import _db

SOURCE = "The Kestrel checkpoint was recalled at rank 1 in 2004.6 ms."
GOOD = "The Kestrel checkpoint recall took 2004.6 ms"
BAD = "The recall happened on June 1st, 2004"


class _Batch(list):
    review_candidates = (AtomicFact(content=BAD),)


def _assemble(db, record, batch):
    db.store_memory(record)
    return source_fact_set(batch, record.content, record, [], db=db)


def test_rejected_candidate_is_stored_with_lineage_but_not_returned_for_enrichment(tmp_path):
    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=SOURCE, scope="shared", shared_with=["peer"])
    live, protected = _assemble(db, record, _Batch([AtomicFact(content=GOOD)]))
    assert {f.content for f in live} == {GOOD, SOURCE}
    assert len(protected) == 1
    rows = db.execute("SELECT f.*,l.unresolved_reason FROM atomic_facts f "
                      "JOIN derivation_lineage l ON l.object_id=f.fact_id")
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["content"] == BAD and row["quarantined"] == 1
    assert row["memory_id"] == record.memory_id and row["scope"] == "shared"
    assert row["shared_with"] == '["peer"]'
    assert row["unresolved_reason"].startswith("source_fidelity:number_became_date")
    assert db.get_all_facts("default") == []


def test_candidate_is_never_committed_live_even_between_insert_and_quarantine(tmp_path):
    db = _db(tmp_path / "memory.db")
    observer = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=SOURCE)
    insert = db.insert_fact_immutable

    def observe_insert(fact):
        result = insert(fact)
        assert observer.get_all_facts("default") == [], "no uncommitted row leaks"
        return result

    with patch.object(db, "insert_fact_immutable", side_effect=observe_insert) as spy:
        _assemble(db, record, _Batch())
    assert spy.call_count == 1
    assert observer.get_all_facts("default") == []


def test_repeat_materialization_keeps_one_candidate_and_preserves_release(tmp_path, monkeypatch):
    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=SOURCE)
    _assemble(db, record, _Batch())
    _assemble(db, record, _Batch())
    rows = db.execute("SELECT fact_id,quarantined FROM atomic_facts")
    assert len(rows) == 1 and rows[0]["quarantined"] == 1
    from superlocalmemory.cli.fidelity_cmd import _release

    monkeypatch.setattr("superlocalmemory.cli.daemon.owned_daemon_process_alive", lambda: False)
    assert _release(str(db.db_path), rows[0]["fact_id"])[0]
    _assemble(db, record, _Batch())
    assert len(db.get_all_facts("default")) == 1
    assert db.execute("SELECT count(*) AS n FROM atomic_facts")[0]["n"] == 1


@pytest.mark.parametrize("candidate", [BAD, "The recall took 2104.6 ms"])
def test_alternate_extractors_cannot_silently_discard_damage(tmp_path, candidate):
    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=SOURCE)
    live, _ = _assemble(db, record, [AtomicFact(content=candidate)])
    assert [f.content for f in live] == [SOURCE]
    assert db.execute("SELECT content,quarantined FROM atomic_facts")[0]["quarantined"] == 1


def test_quarantine_failure_rolls_back_candidate_and_lineage(tmp_path):
    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=SOURCE)
    from superlocalmemory.core import derivation_lineage

    with patch.object(derivation_lineage, "_record", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            _assemble(db, record, _Batch())
    assert db.execute("SELECT fact_id FROM atomic_facts") == []
    assert db.execute("SELECT lineage_id FROM derivation_lineage") == []


def test_review_candidates_do_not_consume_the_five_supported_fact_slots(tmp_path):
    db = _db(tmp_path / "memory.db")
    source = " ".join(f"The Kestrel checkpoint uses channel {i}." for i in range(5))
    record = MemoryRecord(content=source)
    live, _ = _assemble(db, record, _Batch([
        replace(AtomicFact(), content=f"The Kestrel checkpoint uses channel {i}.")
        for i in range(5)
    ]))
    assert len(live) == 6
    assert len(db.execute("SELECT fact_id FROM atomic_facts WHERE quarantined=1")) == 1


def test_store_cannot_commit_a_candidate_when_quarantine_is_unavailable(tmp_path):
    db = _db(tmp_path / "memory.db")
    db.execute("ALTER TABLE atomic_facts DROP COLUMN quarantined")
    record = MemoryRecord(content=SOURCE)
    with pytest.raises(RuntimeError, match="quarantine"):
        _assemble(db, record, _Batch())
    assert db.execute("SELECT fact_id FROM atomic_facts") == []
