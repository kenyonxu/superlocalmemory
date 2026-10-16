# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Acceptance: SLM learns from use — on a real store, end to end.

One store, real engine, real retrieval pipeline (only the embedder is a
deterministic bag-of-words stand-in), adaptive ranking ON
(``SLM_RANKING=v2-ensemble``). The chain under test is the one the audit
found open at every hop:

    recall -> query_id (one id: response == bandit play)          BRN-13
    report over MCP and over REST, through ONE writer             BRN-05
    duplicate reports are one event                               BRN-05
    settlement -> exactly one posterior change per report,
                  repeated passes change nothing                  BRN-11
    a negative control (no report) is not settled by evidence
    the reported memories move in the ranking, in the right direction
    exposures + labels -> a non-empty training set -> a candidate
    ranker trained and persisted, with held-out metrics           BRN-12
    the learning state says what happened, from evidence          BRN-12/21

The promotion path (promote AND reject reachable) is proven on the same
production code in tests/test_core/test_shadow_router_reaches_a_verdict.py
and tests/test_learning/test_shadow_test.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

QUERY = "deploy runbook restart payment service"
FACT_A = "Deploy runbook: restart the payment service first, then the ledger."
FACT_B = "Deploy runbook: restart the payment service last, after the ledger."
FILLER = (
    "The office plants are watered on Mondays.",
    "Lunch orders close at eleven thirty.",
    "The staging database is refreshed every Sunday night.",
    "Team retro happens every other Friday afternoon.",
)


def _embed(text: str) -> list[float]:
    vec = np.zeros(768, dtype=np.float32)
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        vec[int(hashlib.sha256(token.encode()).hexdigest(), 16) % 768] += 1.0
    norm = float(np.linalg.norm(vec))
    return (vec / norm).tolist() if norm else (vec + 1e-3).tolist()


class _Server:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from superlocalmemory.core import shadow_router
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine
    from superlocalmemory.core.engine_ingestion import (
        canonical_store,
        local_trusted_actor_id,
    )
    from superlocalmemory.learning import signals
    from superlocalmemory.learning.bandit import close_threadlocal_conn
    from superlocalmemory.storage.models import Mode

    base = tmp_path / "store"
    monkeypatch.setenv("SLM_DATA_DIR", str(base))
    monkeypatch.setenv("SLM_RANKING", "v2-ensemble")
    shadow_router.reset_for_testing()
    signals._drain_queue_for_tests()
    embedder = MagicMock()
    embedder.embed.side_effect = _embed
    embedder.embed_batch.side_effect = lambda texts: [_embed(t) for t in texts]
    embedder.encode.side_effect = _embed
    embedder.is_available = True
    embedder.compute_fisher_params.return_value = ([0.0] * 768, [1.0] * 768)
    config = SLMConfig.for_mode(Mode.A, base_dir=base)
    config.retrieval.use_cross_encoder = False
    engine = MemoryEngine(config)
    with patch("superlocalmemory.core.engine_wiring.init_embedder",
               return_value=embedder):
        engine.initialize()
        engine._embedder = embedder
    ids = random.Random(7)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(uuid, "uuid4",
                   lambda: uuid.UUID(int=ids.getrandbits(128), version=4))
        for content in (FACT_A, FACT_B, *FILLER):
            canonical_store(engine, content, source_type="python-api",
                            trusted_actor_id=local_trusted_actor_id("python-api"),
                            require_complete=True)
    yield engine, base
    close_threadlocal_conn()
    signals._drain_queue_for_tests()
    shadow_router.reset_for_testing()
    engine.close()


def _fact_id(engine, content: str) -> str:
    rows = engine._db.execute(
        "SELECT fact_id FROM atomic_facts WHERE content = ?", (content,))
    return str(dict(rows[0])["fact_id"])


def _recall(engine):
    return engine.recall(QUERY, limit=6, fast=True)


def _rank(response, fact_id: str) -> int:
    ids = [r.fact.fact_id for r in response.results]
    return ids.index(fact_id) if fact_id in ids else len(ids)


def _plays(learning: Path) -> list[dict]:
    with sqlite3.connect(learning) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT play_id, query_id, settled_at, settlement_type, outcome_id,"
            " shown_fact_ids FROM bandit_plays ORDER BY play_id")]


def _posterior(learning: Path) -> dict[tuple[str, str], tuple[float, float, int]]:
    with sqlite3.connect(learning) as conn:
        return {(r[0], r[1]): (r[2], r[3], r[4]) for r in conn.execute(
            "SELECT stratum, arm_id, alpha, beta, plays FROM bandit_arms")}


def _mcp_report(engine, **kw) -> dict:
    from superlocalmemory.mcp.tools_v28 import register_v28_tools

    server = _Server()
    register_v28_tools(server, lambda: engine)
    return asyncio.run(server.tools["report_outcome"](**kw))


def _rest_client(base: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from superlocalmemory.server.routes import behavioral

    monkeypatch.setattr(behavioral, "MEMORY_DIR", base)
    monkeypatch.setattr(behavioral, "get_active_profile", lambda: "default")
    monkeypatch.setattr(behavioral, "_require_write", lambda request: None)
    app = FastAPI()
    app.include_router(behavioral.router)
    return TestClient(app)


def _settle(base: Path) -> int:
    from superlocalmemory.learning.reward_from_outcomes import settle_from_outcomes

    return settle_from_outcomes("default", base / "learning.db",
                                base / "memory.db")


def test_reported_outcomes_settle_once_and_move_the_ranking(
        world, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, base = world
    learning = base / "learning.db"
    fact_a, fact_b = _fact_id(engine, FACT_A), _fact_id(engine, FACT_B)

    first = _recall(engine)
    assert {fact_a, fact_b} <= {r.fact.fact_id for r in first.results}
    gap_before = _rank(first, fact_b) - _rank(first, fact_a)
    # BRN-13: the id recall returns IS the id the bandit recorded.
    plays = _plays(learning)
    assert [p["query_id"] for p in plays] == [first.query_id]

    second = _recall(engine)
    control = _recall(engine)  # negative control: never reported on
    assert len({first.query_id, second.query_id, control.query_id}) == 3

    # The verdicts: B helped (MCP), A did not (REST). Each sent twice.
    for _ in range(2):
        mcp = _mcp_report(engine, memory_ids=fact_b, outcome="success",
                          recall_query_id=first.query_id, agent_id="agent-x")
        assert mcp["success"] and mcp["reward"] == 1.0 and mcp["attributed"]
    assert mcp["duplicate"] is True
    client = _rest_client(base, monkeypatch)
    for _ in range(2):
        rest = client.post("/api/behavioral/report-outcome", json={
            "memory_ids": [fact_a], "outcome": "failure",
            "recall_query_id": second.query_id}).json()
        assert rest["success"] and rest["reward"] == 0.0 and rest["attributed"]
    assert rest["duplicate"] is True

    with sqlite3.connect(base / "memory.db") as conn:
        rows = conn.execute(
            "SELECT outcome_id, reward, settled, recall_query_id, context_json "
            "FROM action_outcomes WHERE recall_query_id != ''").fetchall()
    assert len(rows) == 2, "a re-sent report must be one event"
    by_qid = {r[3]: r for r in rows}
    assert by_qid[first.query_id][1:3] == (1.0, 1)
    assert by_qid[second.query_id][1:3] == (0.0, 1)
    assert json.loads(by_qid[first.query_id][4])["_provenance"]["surface"] == "mcp"
    assert json.loads(by_qid[second.query_id][4])["_provenance"]["surface"] == "rest"

    before = _posterior(learning)
    assert _settle(base) == 2
    after = _posterior(learning)
    changed = {k: (after[k][0] - before.get(k, (1.0, 1.0, 0))[0],
                   after[k][1] - before.get(k, (1.0, 1.0, 0))[1],
                   after[k][2] - before.get(k, (1.0, 1.0, 0))[2])
               for k in after if after[k] != before.get(k)}
    # Exactly one posterior update per report: +1 alpha (success) and
    # +1 beta (failure) in total, two plays.
    assert sum(d[2] for d in changed.values()) == 2
    assert sum(d[0] for d in changed.values()) == pytest.approx(1.0)
    assert sum(d[1] for d in changed.values()) == pytest.approx(1.0)

    settled = {p["query_id"]: p for p in _plays(learning)}
    assert settled[first.query_id]["outcome_id"] == by_qid[first.query_id][0]
    assert settled[second.query_id]["outcome_id"] == by_qid[second.query_id][0]
    assert settled[control.query_id]["settled_at"] is None  # control untouched

    # BRN-11: further scheduler passes spend nothing again.
    for _ in range(3):
        assert _settle(base) == 0
    assert _posterior(learning) == after

    # The ranking moves the way the evidence says.
    later = _recall(engine)
    gap_after = _rank(later, fact_b) - _rank(later, fact_a)
    print(f"ACCEPTANCE ranks before: B={_rank(first, fact_b)} A={_rank(first, fact_a)}"
          f" after: B={_rank(later, fact_b)} A={_rank(later, fact_a)};"
          f" posterior deltas={sorted(changed.values())}")
    assert gap_after < gap_before, (gap_before, gap_after)
    assert _rank(later, fact_b) < _rank(later, fact_a)

    from superlocalmemory.learning.learning_state import learning_readiness

    state = learning_readiness(learning, base / "memory.db", "default")
    assert state["learning_demonstrated"] is True
    assert state["settled_from_outcomes"] == 2
    assert state["labelled_outcomes"] == 2
    assert state["status"] == "collecting" and state["active_model"] is None


def _drain_exposures(learning: Path) -> int:
    from superlocalmemory.learning import signals

    q = signals.get_queue()
    written = 0
    conn = sqlite3.connect(learning)
    try:
        while not q.empty():
            written += len(signals.record_signal_batch(conn, q.get_nowait()))
    finally:
        conn.close()
    return written


def test_reports_train_a_candidate_ranker(world) -> None:
    """BRN-12: public recall -> report calls produce a non-empty labelled
    training set across learning.db and memory.db, and the shadow cycle
    trains and persists a candidate with held-out metrics."""
    from superlocalmemory.learning.consolidation_worker import _run_shadow_cycle
    from superlocalmemory.learning.database import LearningDatabase

    engine, base = world
    learning = base / "learning.db"
    fact_a, fact_b = _fact_id(engine, FACT_A), _fact_id(engine, FACT_B)
    for _ in range(40):
        response = _recall(engine)
        _mcp_report(engine, memory_ids=fact_b, outcome="success",
                    recall_query_id=response.query_id)
        _mcp_report(engine, memory_ids=fact_a, outcome="failure",
                    recall_query_id=response.query_id)
    assert _drain_exposures(learning) > 0

    rows = LearningDatabase(learning).fetch_training_examples(
        profile_id="default", min_outcome_age_sec=0)
    labelled = [r for r in rows if r["label_source"] == "outcome"]
    assert len(labelled) == 80
    assert {r["fact_id"]: r["outcome_reward"] for r in labelled} == {
        fact_b: 1.0, fact_a: 0.0}

    # The trainer only uses outcomes at least a minute old (a report may still
    # be corrected); age these by five minutes instead of sleeping.
    with sqlite3.connect(base / "memory.db") as conn:
        conn.execute("UPDATE action_outcomes SET timestamp = "
                     "strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-5 minutes')")
    out = _run_shadow_cycle(memory_db_path=str(base / "memory.db"),
                            learning_db_path=str(learning), profile_id="default")
    assert out["aborted"] is None, out
    assert out["candidate_persisted"] is True
    with sqlite3.connect(learning) as conn:
        cand = conn.execute(
            "SELECT trained_on_count, metrics_json FROM learning_model_state "
            "WHERE is_candidate = 1").fetchone()
    assert cand is not None and cand[0] > 0
    print(f"ACCEPTANCE training rows={len(rows)} labelled={len(labelled)} "
          f"trained_on={cand[0]} metrics={cand[1]}")

    from superlocalmemory.learning.learning_state import learning_readiness

    state = learning_readiness(learning, base / "memory.db", "default")
    assert state["status"] == "testing"
    assert state["candidate"] is not None and state["active_model"] is None
