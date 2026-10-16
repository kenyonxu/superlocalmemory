# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Human approval checks the persisted artifact identity before moving files."""

from superlocalmemory.evolution.types import EvolutionStatus

APPROVABLE = ("verified_quarantined", "promoted")


def _candidate(store, record_id, profile_id, recover=None):
    record = store.get_record(record_id, profile_id)
    if record is None:
        return None, "", {"success": False, "error": "No such evolution candidate"}
    if recover is not None:
        record = recover(record)
    latest = store.get_latest_status(record_id, profile_id)
    current = latest.value if latest is not None else getattr(record.status, "value", "")
    if current not in APPROVABLE:
        return (
            record,
            current,
            {
                "success": False,
                "status": current,
                "error": (
                    f"Candidate is {current!r}; only a verified candidate "
                    "awaiting approval can be activated"
                ),
            },
        )
    if not record.quarantine_dir_name:
        return (
            record,
            current,
            {"success": False, "error": "Candidate has no quarantined artifact to activate"},
        )
    return record, current, None


def _activation(store, profile_id, record, expected_hash, activator):
    try:
        return activator.activate(
            record.skill_name,
            record.quarantine_dir_name,
            actor_id="dashboard",
            expected_hash=expected_hash,
            source_path=record.source_skill_path,
        )
    except Exception as exc:
        store.append_transition(
            record.id,
            profile_id,
            EvolutionStatus.APPROVED,
            EvolutionStatus.REJECTED,
            actor_id="dashboard",
            reason=f"activation failed: {exc}",
        )
        raise


def approve(store, profile_id, record_id, reviewed_hash, activator, *,
            recover=None, active_metadata=None):
    record, current, refused = _candidate(store, record_id, profile_id, recover)
    if refused:
        return refused
    expected_hash = record.verified_content_hash or reviewed_hash
    if not expected_hash:
        return {
            "success": False,
            "error": "Legacy artifact requires a reviewed_hash before approval",
        }
    store.append_transition(
        record_id,
        profile_id,
        EvolutionStatus(current),
        EvolutionStatus.APPROVED,
        actor_id="dashboard",
        reason="approved by request",
    )
    activation = _activation(store, profile_id, record, expected_hash, activator)
    store.append_transition(
        record_id,
        profile_id,
        EvolutionStatus.APPROVED,
        EvolutionStatus.ACTIVE,
        actor_id="dashboard",
        reason="activated from quarantine",
        metadata={"content_hash": activation.get("content_hash", ""),
                  **(active_metadata or {})},
    )
    return {
        "success": True,
        "record_id": record_id,
        "skill_name": record.skill_name,
        "status": EvolutionStatus.ACTIVE.value,
        "live_path": activation.get("live_path"),
        "backup_path": activation.get("backup_path"),
        "content_hash": activation.get("content_hash"),
        "rollback": "POST /api/evolution/rollback",
    }
