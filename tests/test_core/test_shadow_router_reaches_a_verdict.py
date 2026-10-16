# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The production promotion path reaches BOTH verdicts (audit BRN-01).

No fakes: the real migrations build learning.db, a candidate row is inserted
the way ``_persist_candidate`` does, recalls are assigned through the router
exactly as ``shadow_serving`` does at serve time, and rewards arrive through
``feed_recall_settled`` exactly as settlement delivers them. Covers a restart
in the middle, replayed query ids, and the old cache-key split.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from superlocalmemory.core import security_primitives as sp
    from superlocalmemory.core import shadow_router

    token = tmp_path / ".install_token"
    token.write_text("ab" * 32, encoding="utf-8")
    monkeypatch.setattr(sp, "_install_token_path", lambda: token)
    shadow_router.reset_for_testing()
    yield
    shadow_router.reset_for_testing()


def _store(tmp_path: Path) -> Path:
    from superlocalmemory.learning.database import LearningDatabase
    from superlocalmemory.storage.migration_runner import apply_all

    learning = tmp_path / "learning.db"
    LearningDatabase(learning)
    apply_all(learning, tmp_path / "memory.db")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(learning) as conn:
        for active, cand in ((1, 0), (0, 1)):
            conn.execute(
                "INSERT INTO learning_model_state (profile_id, state_bytes, "
                "bytes_sha256, is_active, is_candidate, trained_at, updated_at,"
                " metadata_json) VALUES ('p', ?, ?, ?, ?, ?, ?, '{}')",
                (b"m%d" % cand, "0" * 64, active, cand, now, now))
    return learning


def _ids(learning: Path) -> tuple[int, int]:
    with sqlite3.connect(learning) as conn:
        a = conn.execute("SELECT id FROM learning_model_state "
                         "WHERE is_active=1").fetchone()[0]
        c = conn.execute("SELECT id FROM learning_model_state "
                         "WHERE is_candidate=1").fetchone()
    return a, (c[0] if c else None)


def _run(learning: Path, p_active: float, p_cand: float, n: int,
         *, restart_at: int | None = None) -> str | None:
    from superlocalmemory.core import shadow_router as sr
    from superlocalmemory.core.recall_pipeline import feed_recall_settled

    rng = random.Random(5)
    active_id, _ = _ids(learning)
    verdict = None
    for i in range(n):
        if restart_at is not None and i == restart_at:
            sr.reset_for_testing()  # a daemon restart: in-memory state gone
        router = sr.get_shadow_router(learning_db=str(learning), profile_id="p")
        qid = f"q{i}"
        assignment = router.assign(qid, active_model_id=active_id)
        if assignment is None:
            break
        p = p_cand if assignment.arm == sr.ARM_CANDIDATE else p_active
        reward = 1.0 if rng.random() < p else 0.0
        feed_recall_settled(learning_db=str(learning), profile_id="p",
                            query_id=qid, reward=reward)
        # A replay of the same settlement must change nothing.
        feed_recall_settled(learning_db=str(learning), profile_id="p",
                            query_id=qid, reward=1.0 - reward)
        if _ids(learning)[1] is None:
            verdict = "decided"
            break
    return verdict


def test_a_better_candidate_is_promoted_across_a_restart(tmp_path: Path) -> None:
    learning = _store(tmp_path)
    _, cand = _ids(learning)
    assert _run(learning, 0.2, 0.9, 3000, restart_at=40) == "decided"
    with sqlite3.connect(learning) as conn:
        active, results = conn.execute(
            "SELECT id, shadow_results_json FROM learning_model_state "
            "WHERE is_active=1").fetchone()
    assert active == cand
    assert '"verdict": "promoted"' in results


def test_a_worse_candidate_is_rejected_and_stays_rejected(tmp_path: Path) -> None:
    from superlocalmemory.core import shadow_router as sr

    learning = _store(tmp_path)
    active_before, cand = _ids(learning)
    assert _run(learning, 0.9, 0.2, 3000) == "decided"
    with sqlite3.connect(learning) as conn:
        row = conn.execute(
            "SELECT is_active, is_candidate, shadow_results_json "
            "FROM learning_model_state WHERE id=?", (cand,)).fetchone()
    assert row[:2] == (0, 0)
    assert '"verdict": "rejected"' in row[2]
    assert _ids(learning)[0] == active_before
    sr.reset_for_testing()
    router = sr.get_shadow_router(learning_db=str(learning), profile_id="p")
    assert router.refresh_candidate(force=True) is None  # not re-attached


def test_assignment_is_persisted_with_the_served_model(tmp_path: Path) -> None:
    from superlocalmemory.core import shadow_router as sr

    learning = _store(tmp_path)
    active_id, cand = _ids(learning)
    router = sr.get_shadow_router(learning_db=str(learning), profile_id="p")
    seen = {router.assign(f"x{i}", active_model_id=active_id) for i in range(40)}
    assert {a.arm for a in seen} == {sr.ARM_BASELINE, sr.ARM_CANDIDATE}
    for a in seen:
        assert a.served_model_id == (cand if a.arm == sr.ARM_CANDIDATE
                                     else active_id)
    first = router.assign("x0", active_model_id=active_id)
    assert router.assign("x0", active_model_id=None) == first  # replay


def test_unserved_queries_are_not_credited(tmp_path: Path) -> None:
    from superlocalmemory.core import shadow_router as sr

    learning = _store(tmp_path)
    router = sr.get_shadow_router(learning_db=str(learning), profile_id="p")
    for i in range(200):
        router.on_recall_settled(query_id=f"never-served-{i}", reward=1.0)
    assert router._shadow.decide()[1]["n_candidate"] == 0  # noqa: SLF001


def test_the_trainer_and_the_settler_share_one_router(tmp_path: Path) -> None:
    from superlocalmemory.core import shadow_router as sr

    learning = _store(tmp_path)
    a = sr.get_shadow_router(memory_db="", learning_db=str(learning),
                             profile_id="p")
    b = sr.get_shadow_router(memory_db=str(tmp_path / "memory.db"),
                             learning_db=str(learning), profile_id="p")
    assert a is b
