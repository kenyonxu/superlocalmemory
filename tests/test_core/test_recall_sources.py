# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Fixture sources preserve recall identity, ownership and borrowed store data."""

from superlocalmemory.retrieval.source_fidelity_flags import flag_unverified
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import (
    AtomicFact,
    CanonicalEntity,
    RecallResponse,
    RetrievalResult,
)
from tests._recall_sources import recall_sources


def test_fresh_stores_keep_source_ids_repeatable_and_check_each_owner(tmp_path):
    ids = []
    for _ in range(2):
        results = [RetrievalResult(fact=AtomicFact(
            fact_id=f"f-{owner}", profile_id=owner, content=f"Memory for {owner}."),
            score=0.7, evidence_chain=["bm25"]) for owner in ("default", "peer")]
        with recall_sources(tmp_path, results) as db:
            path = db.db_path
            response = RecallResponse(results=results)
            assert flag_unverified(results, db, "default", response=response) == results
            assert response.source_fidelity_status == "checked"
            for result in results:
                source = db.execute(
                    "SELECT content FROM memories WHERE memory_id=? AND profile_id=?",
                    (result.fact.memory_id, result.fact.profile_id),
                )[0]
                assert source["content"] == result.fact.content
                assert result.score == 0.7 and result.evidence_chain == ["bm25"]
            ids.append([r.fact.memory_id for r in results])
        assert not path.exists(), "owned fixture stores must close and clean up"
    assert ids[0] == ids[1]


def test_borrowed_store_keeps_entities_and_stays_open(tmp_path):
    with DatabaseManager(tmp_path / "borrowed.db") as db:
        db.initialize(schema)
        entity = db.store_entity(CanonicalEntity(canonical_name="Atlas"))
        results = [RetrievalResult(fact=AtomicFact(fact_id="f", content="Atlas is ready.",
                                                 canonical_entities=[entity]))]
        with recall_sources(tmp_path, results, db=db) as sources:
            assert sources is db
            response = RecallResponse(results=results)
            assert flag_unverified(results, db, "default", response=response) == results
            assert response.source_fidelity_status == "checked"
        assert db.db_path.exists()
        assert db.execute("SELECT content FROM memories WHERE memory_id=?",
                          (results[0].fact.memory_id,))[0]["content"] == "Atlas is ready."
        assert db.execute("SELECT COUNT(*) AS n FROM canonical_entities")[0]["n"] == 1
