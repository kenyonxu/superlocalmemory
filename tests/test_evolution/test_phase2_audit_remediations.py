# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Activation and append-only audit edge cases, kept within the test file cap."""

import hashlib
import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.evolution.blind_verifier import VerificationResult
from superlocalmemory.evolution.evolution_store import EvolutionStore
from superlocalmemory.evolution.skill_activator import SkillActivationError
from superlocalmemory.evolution.types import EvolutionStatus
from tests.test_evolution.test_phase2_governance import _make_candidate, _make_evolver, _make_record


class TestAuditRemediations:
    """Locks the delivery-lead fixes for the P2 findings."""

    @pytest.fixture
    def activator(self, tmp_path):
        from superlocalmemory.evolution.skill_activator import SkillActivator

        return SkillActivator(
            live_root=tmp_path / "live",
            backup_root=tmp_path / "backup",
            quarantine_root=tmp_path / "quarantine",
        )

    # --- P0-1 / P1-4: quarantine_dir_name sandbox ---
    def test_quarantine_dir_absolute_path_rejected(self, activator, tmp_path):
        evil = tmp_path / "evil"
        evil.mkdir()
        (evil / "SKILL.md").write_text("PWNED", encoding="utf-8")
        with pytest.raises(ValueError):
            activator.activate("victim", str(evil))
        assert not (tmp_path / "live" / "victim" / "SKILL.md").exists()

    def test_quarantine_dir_dotdot_rejected(self, activator):
        with pytest.raises(ValueError):
            activator.activate("victim", "../evil")

    def test_quarantine_dir_symlink_escape_rejected(self, activator, tmp_path):
        (tmp_path / "quarantine").mkdir(parents=True, exist_ok=True)
        evil = tmp_path / "evil"
        evil.mkdir()
        (evil / "SKILL.md").write_text("PWNED", encoding="utf-8")
        link = tmp_path / "quarantine" / "linkdir"
        try:
            os.symlink(evil, link)
        except OSError:
            pytest.skip("symlinks not supported on this platform")
        with pytest.raises(ValueError):
            activator.activate("victim", "linkdir")
        assert not (tmp_path / "live" / "victim" / "SKILL.md").exists()

    # --- P2-4: atomic rollback leaves no torn/temp state ---
    def test_rollback_is_atomic_no_tmp(self, activator, tmp_path):
        q = tmp_path / "quarantine" / "s-v1"
        q.mkdir(parents=True)
        (q / "SKILL.md").write_text("new", encoding="utf-8")
        live_dir = tmp_path / "live" / "s"
        live_dir.mkdir(parents=True)
        (live_dir / "SKILL.md").write_text("orig", encoding="utf-8")
        activator.activate("s", "s-v1")
        activator.rollback("s")
        assert list(live_dir.glob("*.tmp")) == []
        assert (live_dir / "SKILL.md").read_text(encoding="utf-8") == "orig"

    # --- P1-3: DELETE is forbidden (append-only, not just UPDATE) ---
    def test_delete_transition_row_forbidden(self, tmp_path):
        store = EvolutionStore(str(tmp_path / "evo.db"))
        store.insert_record(_make_record("r1"), "default")
        store.append_transition(
            "r1",
            "default",
            EvolutionStatus.CANDIDATE,
            EvolutionStatus.VERIFIED_QUARANTINED,
        )
        conn = sqlite3.connect(str(tmp_path / "evo.db"))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM skill_evolution_transitions WHERE record_id='r1'")
                conn.commit()
        finally:
            conn.rollback()
            conn.close()
        assert len(store.get_transitions("r1", "default")) == 1

    # --- P2-3: hash covers reason + metadata (canonical delimited payload) ---
    def test_hash_covers_reason_and_metadata(self, tmp_path):
        store = EvolutionStore(str(tmp_path / "evo.db"))
        store.insert_record(_make_record("r1"), "default")
        meta = {"quarantine_dir_name": "s-v1"}
        h = store.append_transition(
            "r1",
            "default",
            EvolutionStatus.CANDIDATE,
            EvolutionStatus.VERIFIED_QUARANTINED,
            actor_id="auto",
            reason="blind_verified",
            metadata=meta,
        )
        row = store.get_transitions("r1", "default")[0]
        recomputed = hashlib.sha256(
            "|".join(
                (
                    row["prev_hash"],
                    "r1",
                    "candidate",
                    "verified_quarantined",
                    row["transitioned_at"],
                    "auto",
                    "blind_verified",
                    json.dumps(meta, sort_keys=True),
                )
            ).encode("utf-8")
        ).hexdigest()
        assert recomputed == h == row["transition_hash"]

    # --- P0-2: attempt budget excludes successes, counts failures ---
    def test_budget_excludes_active_successes(self, tmp_path):
        store = EvolutionStore(str(tmp_path / "evo.db"))
        for i in range(3):
            rid = f"a{i}"
            store.insert_record(_make_record(rid, skill_name="sx"), "default")
            store.append_transition(
                rid, "default", EvolutionStatus.CANDIDATE, EvolutionStatus.VERIFIED_QUARANTINED
            )
            store.append_transition(
                rid, "default", EvolutionStatus.VERIFIED_QUARANTINED, EvolutionStatus.APPROVED
            )
            store.append_transition(
                rid, "default", EvolutionStatus.APPROVED, EvolutionStatus.ACTIVE
            )
        assert store.count_attempts("sx", "default") == 0
        assert store.has_exceeded_attempts("sx", "default") is False

    def test_budget_counts_rejected_failures(self, tmp_path):
        store = EvolutionStore(str(tmp_path / "evo.db"))
        for i in range(3):
            rid = f"f{i}"
            store.insert_record(_make_record(rid, skill_name="sf"), "default")
            store.append_transition(
                rid, "default", EvolutionStatus.CANDIDATE, EvolutionStatus.REJECTED
            )
        assert store.has_exceeded_attempts("sf", "default") is True

    # --- P1-1 / P1-2: activation failure rolls back AND records a terminal ---
    def test_activation_failure_rolls_back_and_marks_terminal(self, tmp_path):
        evolver = _make_evolver(tmp_path)
        mock_cfg = MagicMock()
        mock_cfg.evolution.auto_approve = True
        evolver._config = mock_cfg
        mock_activator = MagicMock()
        mock_activator.activate.side_effect = SkillActivationError("write failed after backup")
        mock_activator.rollback.return_value = {"rolled_back": False, "note": "nothing to rollback"}
        evolver._activator = mock_activator

        candidate = _make_candidate()
        with (
            patch.object(evolver, "_llm_confirm", return_value=True),
            patch.object(
                evolver, "_generate_mutation", return_value="# Evolved\ndescription: s\n\nC."
            ),
            patch.object(
                evolver,
                "_blind_verify",
                return_value=VerificationResult(passed=True, confidence=0.9, reasoning="ok"),
            ),
            patch.object(evolver, "_read_skill_content", return_value="original"),
            patch.object(
                evolver,
                "_write_evolved_skill",
                return_value=(tmp_path / "qdir" / "SKILL.md", "s-vabc12"),
            ),
        ):
            outcome = evolver._process_candidate(candidate, "default")

        assert outcome == "rejected"
        assert mock_activator.rollback.called, "P1-2: rollback must actually be called"
        recent = evolver._store.get_recent("default", limit=1)
        pairs = [
            (t["from_status"], t["to_status"])
            for t in evolver._store.get_transitions(recent[0].id, "default")
        ]
        # P1-1: a terminal transition is recorded (not stuck at APPROVED).
        assert ("approved", "failed") in pairs
