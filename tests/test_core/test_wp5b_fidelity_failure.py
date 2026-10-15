# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""PR-242 finding 1: a failed check cannot admit unchecked derivatives."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from superlocalmemory.core import recall_pipeline
from superlocalmemory.retrieval import source_fidelity_flags
from superlocalmemory.server.recall_serializer import recall_response_metadata
from superlocalmemory.storage.models import (
    AtomicFact,
    MemoryRecord,
    RecallResponse,
    RetrievalResult,
)
from tests.test_core.test_source_fidelity_guard import POLICY_SOURCE, _db

SOURCE = POLICY_SOURCE
DAMAGED = "Publish with approval from the release owner"


def _candidates(tmp_path, *, profile="default", source=SOURCE):
    db = _db(tmp_path / "memory.db")
    if profile != "default":
        db.execute("INSERT INTO profiles (profile_id,name) VALUES (?,?)", (profile, profile))
    record = MemoryRecord(content=source, profile_id=profile, scope="global")
    db.store_memory(record)
    facts = [AtomicFact(memory_id=record.memory_id, profile_id=profile, scope="global",
                        content=text) for text in (DAMAGED, source)]
    for fact in facts:
        db.store_fact(fact)
    results = [RetrievalResult(fact=fact, score=score, evidence_chain=["bm25"])
               for fact, score in zip(facts, (0.9, 0.8))]
    return db, results


def _recall(db, results, config, monkeypatch):
    response = RecallResponse(results=results)
    engine = SimpleNamespace(_sufficiency_judge=None, recall=lambda *a, **k: response)
    monkeypatch.setattr(recall_pipeline, "apply_ranking", lambda r, *a, **k: r)
    return recall_pipeline.run_recall(
        "publish approval release owner", "default", fast=True, config=config,
        retrieval_engine=engine, trust_scorer=None, embedder=None, db=db,
        llm=None, hooks=None,
    )


@pytest.mark.parametrize("profile", ["default", "peer"])
def test_sqlite_lineage_error_keeps_source_and_reports_degraded_fidelity(
    tmp_path, mode_a_config, monkeypatch, profile, caplog,
):
    db, results = _candidates(tmp_path, profile=profile)
    db.execute("DROP TABLE derivation_lineage")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db.execute("SELECT * FROM derivation_lineage")
    response = _recall(db, results, mode_a_config, monkeypatch)
    assert [r.fact.content for r in response.results] == [SOURCE]
    assert response.source_fidelity_status == "degraded"
    metadata = recall_response_metadata(response)
    assert metadata["source_fidelity_status"] == "degraded"
    assert "unchecked derivatives withheld" in metadata["source_fidelity_note"]
    assert "source fidelity" in caplog.text.lower()
    assert DAMAGED not in caplog.text and SOURCE not in caplog.text
    assert response.results[0].score == 0.8
    assert response.results[0].evidence_chain == ["bm25"]
    assert results[0].evidence_chain == ["bm25"]
    rows = db.execute("SELECT content,quarantined FROM atomic_facts ORDER BY content")
    assert {row["content"] for row in rows} == {SOURCE, DAMAGED}
    assert all(row["quarantined"] == 0 for row in rows), "recall must remain read-only"


def test_checker_failure_keeps_only_verified_source_excerpts(tmp_path, monkeypatch):
    source = SOURCE + " " + "Release checklist. " * 30 + "Release owner signs off."
    db, results = _candidates(tmp_path, source=source)
    from superlocalmemory.core.source_excerpt import attach_source_pointer

    excerpt = AtomicFact(memory_id=results[1].fact.memory_id, content=source)
    attach_source_pointer(excerpt, source, 240)
    db.store_fact(excerpt)
    results.append(RetrievalResult(fact=excerpt, score=0.7))

    def broken_check(*args, **kwargs):
        raise ValueError("checker failure")

    monkeypatch.setattr(source_fidelity_flags, "check_fact_against_source", broken_check)
    out = source_fidelity_flags.flag_unverified(results, db, "default")
    assert [r.fact.content for r in out] == [source, excerpt.content]
    assert out == results[1:] and out[0] is results[1]


def test_unreadable_sources_withhold_every_unchecked_derivative(
    tmp_path, mode_a_config, monkeypatch,
):
    db, results = _candidates(tmp_path)
    # The sources become unreadable. Dropped with foreign keys off: with them on,
    # DROP TABLE first deletes every row, the cascade into atomic_facts fires its
    # FTS5 delete trigger in the middle of the schema change, and SQLite refuses
    # with "database table is locked" -- the damage this test simulates is a
    # missing table, not a cascade.
    with db.raw_connection() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE memories")
    response = _recall(db, results, mode_a_config, monkeypatch)
    assert response.results == []
    assert response.abstained is True
    assert recall_response_metadata(response)["source_fidelity_status"] == "degraded"


def test_healthy_fidelity_check_reports_checked(tmp_path, mode_a_config, monkeypatch):
    db, results = _candidates(tmp_path)
    response = _recall(db, results, mode_a_config, monkeypatch)
    assert [r.fact.content for r in response.results] == [SOURCE]
    assert recall_response_metadata(response)["source_fidelity_status"] == "checked"
    assert recall_response_metadata(response)["source_fidelity_note"] is None


@pytest.mark.parametrize("db", [None, object(), SimpleNamespace(db_path=None)])
def test_missing_database_cannot_admit_an_unchecked_fact(db):
    results = [RetrievalResult(fact=AtomicFact(memory_id="missing", content=DAMAGED))]
    response = RecallResponse(results=results)
    assert source_fidelity_flags.flag_unverified(results, db, "default", response=response) == []
    assert response.source_fidelity_status == "unavailable"
    metadata = recall_response_metadata(response)
    assert metadata["source_fidelity_status"] == "unavailable"
    assert metadata["source_fidelity_note"] == (
        "No source store available; unchecked derivatives withheld."
    )


def test_in_memory_store_without_db_path_can_check_fidelity(tmp_path):
    db, results = _candidates(tmp_path)
    with sqlite3.connect(":memory:") as memory:
        memory.row_factory = sqlite3.Row
        with db.raw_connection() as disk:
            disk.backup(memory)
        response = RecallResponse(results=results)
        assert source_fidelity_flags.flag_unverified(
            results, memory, "default", response=response,
        ) == results[1:]
        assert response.source_fidelity_status == "checked"
    db.close()


def test_source_fallback_checks_owner_and_excerpt_bytes(tmp_path):
    db, results = _candidates(tmp_path)
    db.execute("DROP TABLE derivation_lineage")
    source_fact = results[1].fact
    wrong_owner = AtomicFact(memory_id=source_fact.memory_id, content=SOURCE, profile_id="peer")
    forged_excerpt = AtomicFact(
        memory_id=source_fact.memory_id,
        content="Never\n…[content truncated at ingest; full text in source memory]…\nforged tail",
    )
    candidates = [RetrievalResult(fact=fact) for fact in (wrong_owner, forged_excerpt)]
    assert source_fidelity_flags.flag_unverified(candidates, db, "default") == []
