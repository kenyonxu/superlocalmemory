"""BRN-17/29/30: actual SQLite retention, access and source provenance."""

from datetime import UTC, datetime, timedelta

import pytest

from superlocalmemory.core.config import ForgettingConfig
from superlocalmemory.learning.forgetting_scheduler import ForgettingScheduler
from superlocalmemory.math.ebbinghaus import EbbinghausCurve
from superlocalmemory.storage.access_log import AccessLog
from tests.test_learning.test_forgetting_scheduler import db_with_facts as _fixture_db_with_facts

PID = "test_profile"

db_with_facts = _fixture_db_with_facts


def scheduler(db):
    cfg = ForgettingConfig()
    return ForgettingScheduler(db, EbbinghausCurve(cfg), cfg)


def age(db):
    old = (datetime.now(UTC) - timedelta(days=2000)).isoformat()
    db.execute("DELETE FROM fact_access_log")
    db.execute("UPDATE atomic_facts SET created_at=?", (old,))


@pytest.mark.parametrize("protection", ["pin", "reward"])
def test_protected_fact_survives_all_decay_zones(db_with_facts, protection):
    db = db_with_facts
    age(db)
    if protection == "pin":
        db.execute("UPDATE atomic_facts SET pinned=1 WHERE fact_id='fact_009'")
    else:
        db.execute("ALTER TABLE action_outcomes ADD COLUMN reward REAL")
        db.execute("ALTER TABLE action_outcomes ADD COLUMN settled_at TEXT")
        db.execute(
            "INSERT INTO action_outcomes (outcome_id,profile_id,fact_ids_json,reward,settled_at) "
            "VALUES ('o',?, '[\"fact_009\"]',.9,datetime('now'))",
            (PID,),
        )
    scheduler(db).run_decay_cycle(PID, force=True)
    assert (
        db.execute("SELECT lifecycle FROM atomic_facts WHERE fact_id='fact_009'")[0][0] == "active"
    )


def test_source_trust_reaches_curve(db_with_facts):
    db = db_with_facts
    db.execute(
        "INSERT INTO provenance (provenance_id,profile_id,fact_id,created_by) "
        "VALUES ('p',?,'fact_009','untrusted')",
        (PID,),
    )
    db.execute(
        "INSERT INTO trust_scores (trust_id,profile_id,target_type,target_id,trust_score) "
        "VALUES ('t',?,'agent','untrusted',.1)",
        (PID,),
    )
    facts = scheduler(db)._fetch_facts_with_metadata(PID)
    assert next(f for f in facts if f["fact_id"] == "fact_009")["trust_score"] == 0.1


def test_real_access_advances_clock_and_refresh_survives_decay(db_with_facts):
    db = db_with_facts
    age(db)
    sched = scheduler(db)
    sched.run_decay_cycle(PID, force=True)
    assert AccessLog(db).store_access("fact_009", PID, "search")
    clock = db.get_retention("fact_009", PID)["last_accessed_at"]
    assert datetime.fromisoformat(clock).replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(
        minutes=1
    )
    data = next(f for f in sched._fetch_facts_with_metadata(PID) if f["fact_id"] == "fact_009")
    assert datetime.fromisoformat(data["last_accessed_at"]).replace(tzinfo=UTC) > datetime.now(
        UTC
    ) - timedelta(minutes=1)
    sched.on_access_event("fact_009", PID)
    before = db.get_retention("fact_009", PID)
    sched.run_decay_cycle(PID, force=True)
    after = db.get_retention("fact_009", PID)
    assert after["memory_strength"] >= before["memory_strength"]
    assert after["retention_score"] > 0.99


def test_consolidation_is_not_a_genuine_access(db_with_facts):
    db = db_with_facts
    age(db)
    AccessLog(db).store_access("fact_009", PID, "consolidation")
    data = next(
        f for f in scheduler(db)._fetch_facts_with_metadata(PID) if f["fact_id"] == "fact_009"
    )
    assert data["access_count"] == 0
    assert datetime.fromisoformat(data["last_accessed_at"]) < datetime.now(UTC) - timedelta(
        days=1000
    )


def test_direct_fact_trust_overrides_source_and_unknown_is_neutral(db_with_facts):
    db = db_with_facts
    db.execute(
        "INSERT INTO provenance (provenance_id,profile_id,fact_id,created_by) "
        "VALUES ('p',?,'fact_009','unknown')",
        (PID,),
    )
    db.execute(
        "INSERT INTO trust_scores (trust_id,profile_id,target_type,target_id,trust_score) "
        "VALUES ('t',?,'agent','unknown',.99)",
        (PID,),
    )
    first = {f["fact_id"]: f["trust_score"] for f in scheduler(db)._fetch_facts_with_metadata(PID)}
    assert first["fact_009"] == 0.5
    assert first["fact_008"] == 0.5
    db.execute(
        "INSERT INTO trust_scores (trust_id,profile_id,target_type,target_id,trust_score) "
        "VALUES ('direct',?,'fact','fact_009',.2)",
        (PID,),
    )
    second = {f["fact_id"]: f["trust_score"] for f in scheduler(db)._fetch_facts_with_metadata(PID)}
    assert second["fact_009"] == pytest.approx(0.2)


def test_explicit_outcome_writer_refreshes_clock_once(db_with_facts, tmp_path):
    from superlocalmemory.learning.outcome_events import OutcomeReport, write_outcome

    db = db_with_facts
    age(db)
    sched = scheduler(db)
    sched.run_decay_cycle(PID, force=True)
    report = OutcomeReport(
        profile_id=PID,
        fact_ids=("fact_009",),
        outcome="failure",
        surface="python",
        idempotency_key="use-once",
    )
    written = write_outcome(
        report,
        memory_db_path=db.db_path,
        learning_db_path=tmp_path / "learning.db",
        bridge_signals=False,
    )
    assert written.created
    clock = db.get_retention("fact_009", PID)["last_accessed_at"]
    assert datetime.fromisoformat(clock) > datetime.now(UTC) - timedelta(minutes=1)
    duplicate = write_outcome(
        report,
        memory_db_path=db.db_path,
        learning_db_path=tmp_path / "learning.db",
        bridge_signals=False,
    )
    assert not duplicate.created
    assert db.execute("SELECT COUNT(*) FROM fact_access_log WHERE fact_id='fact_009'")[0][0] == 1
    sched.run_decay_cycle(PID, force=True)
    assert db.get_retention("fact_009", PID)["retention_score"] > 0.99


def test_canonical_trust_resolution_uses_bounded_queries(db_with_facts, monkeypatch):
    from superlocalmemory.learning.forgetting_metadata import attach_canonical_trust

    db = db_with_facts
    queries = []
    execute = db.execute

    def traced(sql, params=()):
        queries.append(sql)
        return execute(sql, params)

    monkeypatch.setattr(db, "execute", traced)
    rows = [{"fact_id": f"fact_{i:03d}"} for i in range(10)]
    result = attach_canonical_trust(db, rows, PID)
    assert all(row["trust_score"] == 0.5 for row in result)
    assert len(queries) <= 3
