"""Review-gated correction-case storage contract for M042.

The storage layer must retain only identifiers and lifecycle metadata.  It is
deliberately unable to alter facts, retrieval, or ranking by itself.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from superlocalmemory.storage.correction_cases import (
    CorrectionActor,
    CorrectionAuthorizationError,
    CorrectionCaseStore,
    CorrectionCompareAndSetError,
    propose_on_connection,
)
from superlocalmemory.storage.migrations import M042_correction_case_ledger as m042
from superlocalmemory.storage.write_lock import get_write_lock


@pytest.fixture
def store(tmp_path: Path) -> CorrectionCaseStore:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        m042.apply(conn)
        # Minimal runtime tables needed to prove that an approved case mutates
        # only the predecessor's temporal lifecycle, never fact text.
        conn.execute(
            "CREATE TABLE atomic_facts ("
            "fact_id TEXT PRIMARY KEY, profile_id TEXT, scope TEXT, content TEXT)"
        )
        conn.execute(
            "INSERT INTO atomic_facts VALUES "
            "('old-release', 'alpha', 'project', 'private old text'), "
            "('new-release', 'alpha', 'project', 'private new text'), "
            "('final-release', 'alpha', 'project', 'private final text')"
        )
        conn.execute(
            "CREATE TABLE fact_temporal_validity ("
            "fact_id TEXT PRIMARY KEY, profile_id TEXT, valid_from TEXT, valid_until TEXT, "
            "system_created_at TEXT, system_expired_at TEXT, invalidated_by TEXT, "
            "invalidation_reason TEXT)"
        )
        conn.execute(
            "INSERT INTO fact_temporal_validity VALUES "
            "('old-release', 'alpha', '2026-01-01T00:00:00+00:00', NULL, "
            "'2026-01-01T00:00:00+00:00', NULL, NULL, NULL)"
        )
    return CorrectionCaseStore(
        path,
        is_profile_active=lambda profile_id: profile_id == "alpha",
        is_actor_trusted=lambda actor: actor.actor_id == "reviewer-1",
    )


def _actor() -> CorrectionActor:
    return CorrectionActor(
        actor_id="reviewer-1", actor_kind="human", trust_tier="operator_verified"
    )


def _propose(store: CorrectionCaseStore):
    return store.propose(
        case_id="case-1",
        profile_id="alpha",
        scope="project",
        predecessor_fact_id="old-release",
        successor_fact_id="new-release",
        reason_code="release_state_replaced",
        actor=_actor(),
        idempotency_key="proposal-1",
    )


def test_m042_has_only_identifier_and_lifecycle_columns(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        m042.apply(conn)
        assert m042.verify(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(correction_cases)")} | {
            row[1] for row in conn.execute("PRAGMA table_info(correction_events)")
        }
    assert not {"content", "fact_text", "raw_text", "query"} & columns


def test_m042_refuses_a_preexisting_lookalike_without_lifecycle_checks(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE correction_cases (case_id TEXT PRIMARY KEY, profile_id TEXT)")
        with pytest.raises(sqlite3.OperationalError, match="malformed"):
            m042.apply(conn)


def test_m042_verify_rejects_an_index_name_reused_on_another_table(tmp_path: Path) -> None:
    """A migration cannot be stamped complete by an unrelated same-named index."""
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        m042.apply(conn)
        conn.execute("DROP INDEX idx_correction_cases_successor_admission")
        conn.execute("CREATE TABLE stale_index_target (profile_id TEXT, fact_id TEXT)")
        conn.execute(
            "CREATE INDEX idx_correction_cases_successor_admission "
            "ON stale_index_target (profile_id, fact_id)"
        )
        assert m042.verify(conn) is False


def test_proposal_is_idempotent_and_does_not_mutate_facts(store: CorrectionCaseStore) -> None:
    created = _propose(store)
    replay = _propose(store)

    assert created.case_id == replay.case_id == "case-1"
    assert created.status == "proposed"
    assert replay.version == 0

    with sqlite3.connect(store.path) as conn:
        row = conn.execute(
            "SELECT content FROM atomic_facts WHERE fact_id='old-release'"
        ).fetchone()
        assert row == ("private old text",)
        assert conn.execute("SELECT COUNT(*) FROM correction_events").fetchone() == (1,)


def test_active_profile_can_list_and_get_identifier_only_correction_cases(
    store: CorrectionCaseStore,
) -> None:
    _propose(store)

    assert store.get_case("case-1").successor_fact_id == "new-release"
    assert [case.case_id for case in store.list_cases("alpha")] == ["case-1"]
    with pytest.raises(CorrectionAuthorizationError):
        store.list_cases("other-profile")


def test_connection_owned_proposal_rolls_back_without_residue(tmp_path: Path) -> None:
    """The canonical writer owns the transaction; this helper must not nest it."""
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        m042.apply(conn)

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        proposal = propose_on_connection(
            conn,
            case_id="case-owned-transaction",
            profile_id="alpha",
            scope="personal",
            predecessor_fact_id="old-release",
            successor_fact_id="new-release",
            reason_code="temporal_contradiction",
            actor=_actor(),
            idempotency_key="proposal-owned-transaction",
            is_profile_active=lambda profile_id: profile_id == "alpha",
            is_actor_trusted=lambda actor: actor.actor_id == "reviewer-1",
        )
        assert proposal.status == "proposed"
        conn.execute("ROLLBACK")
    finally:
        conn.close()

    with sqlite3.connect(path) as verify:
        assert verify.execute("SELECT COUNT(*) FROM correction_cases").fetchone() == (0,)
        assert verify.execute("SELECT COUNT(*) FROM correction_events").fetchone() == (0,)


def test_standalone_correction_transaction_shares_memory_write_lock(
    store: CorrectionCaseStore,
) -> None:
    """A standalone store cannot contend with canonical memory writers in-process."""
    lock = get_write_lock(store.path)
    opened = threading.Event()
    release = threading.Event()

    def hold_transaction() -> None:
        with store._transaction():
            opened.set()
            assert release.wait(timeout=2)

    thread = threading.Thread(target=hold_transaction)
    thread.start()
    assert opened.wait(timeout=2)
    assert lock.acquire(timeout=0.05) is False
    release.set()
    thread.join(timeout=2)
    assert thread.is_alive() is False
    assert lock.acquire(timeout=0.2) is True
    lock.release()


def test_apply_is_review_gated_cas_and_changes_only_reviewed_predecessor_temporal_state(
    store: CorrectionCaseStore,
) -> None:
    _propose(store)

    applied = store.apply(
        "case-1",
        expected_version=0,
        actor=_actor(),
        operation_id="apply-1",
        event_valid_until="2026-08-16T00:00:00+00:00",
    )

    assert applied.status == "applied"
    assert applied.version == 1
    assert applied.system_effective_at is not None
    assert applied.event_valid_until == "2026-08-16T00:00:00+00:00"
    with sqlite3.connect(store.path) as conn:
        event = conn.execute(
            "SELECT event_type, expected_version, resulting_version, system_occurred_at "
            "FROM correction_events WHERE operation_id='apply-1'"
        ).fetchone()
        assert event[0:3] == ("applied", 0, 1)
        assert event[3] is not None
        row = conn.execute(
            "SELECT content FROM atomic_facts WHERE fact_id='old-release'"
        ).fetchone()
        assert row == ("private old text",)
        temporal = conn.execute(
            "SELECT valid_until, system_expired_at, invalidated_by, invalidation_reason "
            "FROM fact_temporal_validity WHERE fact_id='old-release'"
        ).fetchone()
        assert temporal[0] == "2026-08-16T00:00:00+00:00"
        assert temporal[1] is not None
        assert temporal[2:] == ("new-release", "release_state_replaced")

    replay = store.apply("case-1", expected_version=0, actor=_actor(), operation_id="apply-1")
    assert replay == applied
    with pytest.raises(CorrectionCompareAndSetError):
        store.apply("case-1", expected_version=0, actor=_actor(), operation_id="apply-2")


def test_untrusted_actor_cannot_change_case_or_append_event(store: CorrectionCaseStore) -> None:
    _propose(store)
    untrusted = CorrectionActor(actor_id="agent-1", actor_kind="agent", trust_tier="unverified")

    with pytest.raises(CorrectionAuthorizationError):
        store.apply("case-1", expected_version=0, actor=untrusted, operation_id="apply-1")
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT status, version FROM correction_cases").fetchone() == (
            "proposed",
            0,
        )
        assert conn.execute("SELECT COUNT(*) FROM correction_events").fetchone() == (1,)


def test_rollback_is_append_only_and_restores_the_exact_prior_temporal_tuple(
    store: CorrectionCaseStore,
) -> None:
    _propose(store)
    store.apply(
        "case-1",
        expected_version=0,
        actor=_actor(),
        operation_id="apply-1",
        event_valid_until="2026-08-16T00:00:00+00:00",
    )

    rolled_back = store.rollback(
        "case-1", expected_version=1, actor=_actor(), operation_id="rollback-1"
    )

    assert rolled_back.status == "rolled_back"
    assert rolled_back.version == 2
    assert rolled_back.event_valid_until == "2026-08-16T00:00:00+00:00"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT event_type FROM correction_events ORDER BY rowid"
        ).fetchall() == [("proposed",), ("applied",), ("rolled_back",)]
        assert conn.execute(
            "SELECT valid_from, valid_until, system_created_at, system_expired_at, "
            "invalidated_by, invalidation_reason FROM fact_temporal_validity "
            "WHERE fact_id='old-release'"
        ).fetchone() == (
            "2026-01-01T00:00:00+00:00",
            None,
            "2026-01-01T00:00:00+00:00",
            None,
            None,
            None,
        )


def test_rollback_refuses_when_a_live_child_correction_depends_on_the_successor(
    store: CorrectionCaseStore,
) -> None:
    """Restoring an ancestor cannot silently strand a reviewed correction chain."""
    _propose(store)
    store.apply("case-1", expected_version=0, actor=_actor(), operation_id="apply-1")
    store.propose(
        case_id="case-2",
        profile_id="alpha",
        scope="project",
        predecessor_fact_id="new-release",
        successor_fact_id="final-release",
        reason_code="release_state_replaced",
        actor=_actor(),
        idempotency_key="proposal-2",
    )

    with pytest.raises(CorrectionCompareAndSetError, match="dependent"):
        store.rollback("case-1", expected_version=1, actor=_actor(), operation_id="rollback-1")
