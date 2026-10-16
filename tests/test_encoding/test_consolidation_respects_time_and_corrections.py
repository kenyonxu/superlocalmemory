# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Consolidation respects corrections, time and its own archive decisions.

BRN-31: a retired fact must never come back as a fresh, current gist.
BRN-32: equivalent ISO-8601 spellings cluster identically; facts months apart
        stay separate whatever their timestamp format.
BRN-25: consolidating sources is not a use of them; the next decay pass must
        not reactivate what consolidation just archived.
"""

from __future__ import annotations

import json

import pytest

from superlocalmemory.core.config import CCQConfig, ForgettingConfig
from superlocalmemory.encoding.cognitive_consolidator import CognitiveConsolidator
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import _new_id

PROFILE = "p-ccq"
ENTS = ["Juniper", "release"]


@pytest.fixture
def db(tmp_path) -> DatabaseManager:
    from superlocalmemory.storage import schema

    mgr = DatabaseManager(tmp_path / "ccq.db")
    mgr.initialize(schema)
    mgr.execute("INSERT OR IGNORE INTO profiles (profile_id, name) "
                "VALUES (?, 'p')", (PROFILE,))
    return mgr


def _fact(db, content: str, observed: str, *, importance: float = 0.5,
          created: str = "datetime('now')") -> str:
    fid, mid = _new_id(), _new_id()
    db.execute("INSERT INTO memories (memory_id, profile_id, content) "
               "VALUES (?, ?, ?)", (mid, PROFILE, content))
    db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, "
        "fact_type, entities_json, canonical_entities_json, observation_date, "
        "confidence, importance, evidence_count, access_count, lifecycle, "
        f"created_at) VALUES (?, ?, ?, ?, 'semantic', ?, ?, ?, 0.9, ?, 1, 0, "
        f"'active', {created})",
        (fid, mid, PROFILE, content, json.dumps(ENTS), json.dumps(ENTS),
         observed, importance))
    db.execute(
        "INSERT INTO fact_retention (fact_id, profile_id, retention_score, "
        "memory_strength, access_count, last_accessed_at, lifecycle_zone) "
        "VALUES (?, ?, 0.3, 1.0, 0, datetime('now', '-30 days'), 'cold')",
        (fid, PROFILE))
    db.store_temporal_validity(fid, PROFILE)  # as the production writer does
    return fid


def _run(db) -> object:
    return CognitiveConsolidator(
        db=db, config=CCQConfig(use_llm_gist=False, min_cluster_size=2),
    ).run_pipeline(PROFILE)


def _current_contents(db) -> list[str]:
    rows = db.execute(
        "SELECT content FROM atomic_facts f WHERE f.profile_id = ?"
        + db.current_fact_clause("f"), (PROFILE,))
    return [dict(r)["content"] for r in rows]


def test_a_retired_rule_never_returns_as_a_current_gist(db) -> None:
    old = _fact(db, "OLDRETIRED Juniper release may skip security review",
                "2026-01-01", importance=0.99)
    new = _fact(db, "Juniper release requires security review", "2026-01-02")
    _fact(db, "Juniper release notes go to the changelog", "2026-01-03")
    db.invalidate_fact_temporal(old, new, "policy replaced")
    _run(db)
    assert not [c for c in _current_contents(db) if "OLDRETIRED" in c]
    gists = db.execute("SELECT content, confidence FROM atomic_facts "
                       "WHERE session_id = 'ccq'")
    for row in gists:
        assert "OLDRETIRED" not in dict(row)["content"]
        assert dict(row)["confidence"] < 1.0


def test_a_source_retired_after_selection_abandons_the_cluster(db) -> None:
    a = _fact(db, "Juniper release step one", "2026-01-01")
    b = _fact(db, "Juniper release step two", "2026-01-02")
    cons = CognitiveConsolidator(
        db=db, config=CCQConfig(use_llm_gist=False, min_cluster_size=2))
    candidates = cons._step1_identify(PROFILE)
    clusters = cons._step2_cluster(candidates, PROFILE)
    assert clusters and set(clusters[0].fact_ids) == {a, b}
    db.invalidate_fact_temporal(a, b, "superseded mid-run")
    gist = cons._step3_extract_gist(clusters[0], PROFILE)
    with pytest.raises(RuntimeError):
        cons._step5_store_block(clusters[0], gist, PROFILE)
    assert not db.execute("SELECT 1 FROM ccq_consolidated_blocks")


@pytest.mark.parametrize("spell", [
    lambda d: d, lambda d: f"{d}T00:00:00", lambda d: f"{d}T00:00:00+00:00",
    lambda d: f"{d}T00:00:00Z", lambda d: f"{d}T00:00:00.000000+00:00",
])
def test_months_apart_never_merge_whatever_the_spelling(db, spell) -> None:
    for day in ("2026-01-01", "2026-04-01", "2026-07-01"):
        _fact(db, f"Juniper release revision of {day}", spell(day))
    result = _run(db)
    assert result.blocks_created == 0 and result.facts_archived == 0


def test_consolidation_is_not_use_and_does_not_reactivate(db) -> None:
    from superlocalmemory.learning.forgetting_scheduler import ForgettingScheduler
    from superlocalmemory.math.ebbinghaus import EbbinghausCurve

    ids = [_fact(db, f"Juniper release fact {i}", f"2026-01-0{i + 1}",
                 created="datetime('now', '-30 days')") for i in range(3)]
    assert _run(db).blocks_created == 1
    cfg = ForgettingConfig()
    sched = ForgettingScheduler(db, EbbinghausCurve(cfg), cfg)
    for _ in range(2):
        sched.run_decay_cycle(PROFILE, force=True)
    zones = {dict(r)["fact_id"]: dict(r)["lifecycle_zone"] for r in db.execute(
        "SELECT fact_id, lifecycle_zone FROM fact_retention WHERE profile_id = ?",
        (PROFILE,))}
    assert all(zones[i] == "archive" for i in ids), zones
