# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Small governed stages for one mutation, review and optional activation."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from superlocalmemory.evolution import blind_verifier, mutation_generator
from superlocalmemory.evolution.skill_activator import SkillActivationError
from superlocalmemory.evolution.types import EvolutionRecord, EvolutionStatus

logger = logging.getLogger(__name__)


class CandidatePipeline:
    def __init__(self, evolver, candidate, profile_id):
        self.evolver, self.candidate, self.profile_id = evolver, candidate, profile_id
        self.now = datetime.now(UTC).isoformat()
        identity = f"{profile_id}:{candidate.skill_name}:{candidate.trigger.value}:{self.now}"
        self.record_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
        self.original, self.evolved, self.directory = "", "", ""

    def run(self):
        if not self._prepare():
            return "skipped"
        if not self.evolver._llm_confirm(self.candidate, self.original):
            self._transition(
                EvolutionStatus.CANDIDATE,
                EvolutionStatus.REJECTED,
                reason="llm_confirmation_rejected",
            )
            return "rejected"
        prompt = mutation_generator.build_mutation_prompt(self.candidate, self.original)
        self.evolved = self.evolver._generate_mutation(prompt)
        if not self.evolved:
            self._transition(
                EvolutionStatus.CANDIDATE,
                EvolutionStatus.FAILED,
                reason="mutation_generation_failed",
            )
            return "rejected"
        if not self._verify():
            return "rejected"
        self._stage()
        return self._activate() if self.evolver._is_auto_approve() else "quarantined"

    def _prepare(self):
        e, c = self.evolver, self.candidate
        context = hashlib.sha256(json.dumps(list(c.evidence)).encode()).hexdigest()[:12]
        if e._store.is_addressed(c.skill_name, context) or e._store.has_exceeded_attempts(
            c.skill_name, self.profile_id
        ):
            return False
        e._store.mark_addressed(c.skill_name, context)
        self.original = e._read_skill_content(c.skill_name)
        record = EvolutionRecord(
            id=self.record_id,
            skill_name=c.skill_name,
            parent_skill_id=c.skill_name,
            evolution_type=c.evolution_type,
            trigger=c.trigger,
            status=EvolutionStatus.CANDIDATE,
            evidence=c.evidence,
            original_content=self.original[:2000],
            created_at=self.now,
        )
        e._store.insert_record(record, self.profile_id)
        return True

    def _verify(self):
        description = self.evolver._extract_description(self.evolved)
        prompt = blind_verifier.build_verification_prompt(
            self.candidate.skill_name, description, self.evolved
        )
        result = self.evolver._blind_verify(prompt)
        if result.passed:
            return True
        self._transition(
            EvolutionStatus.CANDIDATE,
            EvolutionStatus.REJECTED,
            reason=f"blind_verification: {result.reasoning}",
        )
        return False

    def _stage(self):
        e, c = self.evolver, self.candidate
        _, self.directory = e._write_evolved_skill(c, self.evolved, self.record_id)
        history = e._store.get_skill_history(c.skill_name, self.profile_id, limit=1)
        generation = (
            history[0].generation
            if history and history[0].status == EvolutionStatus.PROMOTED
            else 0
        )
        self.digest = hashlib.sha256(self.evolved.encode()).hexdigest()
        self.source = getattr(e, "_skill_sources", {}).get(c.skill_name, "")
        diff = e._compute_diff(self.original, self.evolved)
        self._transition(
            EvolutionStatus.CANDIDATE,
            EvolutionStatus.VERIFIED_QUARANTINED,
            reason="blind_verified",
            metadata={
                "quarantine_dir_name": self.directory,
                "content_hash": self.digest,
                "source_skill_path": self.source,
                "diff_summary": e._summarize_diff(diff),
                "generation": generation + 1,
            },
        )
        e._store.record_evolution_attempt(self.profile_id)
        self._audit(
            "skill_promotion",
            self.profile_id,
            self.digest,
            {
                "evolution_type": c.evolution_type.value,
                "trigger": c.trigger.value,
                "quarantine_dir": self.directory,
            },
        )

    def _activate(self):
        self._transition(
            EvolutionStatus.VERIFIED_QUARANTINED,
            EvolutionStatus.APPROVED,
            actor_id="auto",
            reason="auto_approved",
        )
        activator = self.evolver._get_activator()
        moved = False
        try:
            result = activator.activate(
                self.candidate.skill_name,
                self.directory,
                actor_id="auto",
                expected_hash=self.digest,
                source_path=self.source,
            )
            moved = True
            self._transition(
                EvolutionStatus.APPROVED,
                EvolutionStatus.ACTIVE,
                actor_id="auto",
                metadata={
                    "live_path": result.get("live_path", ""),
                    "installation_wide": bool(getattr(activator, "_installation_wide", True)),
                },
            )
            self._audit(
                "skill_activation",
                "auto",
                result["content_hash"],
                {"live_path": result["live_path"], "backup_path": result.get("backup_path")},
            )
            return "evolved"
        except (SkillActivationError, FileNotFoundError, ValueError, OSError) as exc:
            return self._activation_failure(activator, exc, moved)

    def _activation_failure(self, activator, exc, moved):
        rolled_back = False
        # A refusal before backup/write must never remove the existing skill.
        if moved or isinstance(exc, SkillActivationError):
            try:
                rolled_back = bool(activator.rollback(self.candidate.skill_name).get("rolled_back"))
            except Exception as rollback_exc:
                logger.error("Skill rollback failed: %s", rollback_exc)
        status = EvolutionStatus.ROLLED_BACK if rolled_back else EvolutionStatus.FAILED
        self._transition(EvolutionStatus.APPROVED, status, actor_id="auto", reason=str(exc))
        self._audit(
            "skill_activation_failed", "auto", "", {"error": str(exc), "rolled_back": rolled_back}
        )
        return "rejected"

    def _transition(self, before, after, **kwargs):
        return self.evolver._store.append_transition(
            self.record_id, self.profile_id, before, after, **kwargs
        )

    def _audit(self, operation, actor, digest, metadata):
        self.evolver._audit.log(
            operation=operation,
            agent_id=actor,
            profile_id=self.profile_id,
            content_hash=digest,
            metadata={
                "record_id": self.record_id,
                "skill_name": self.candidate.skill_name,
                **metadata,
            },
        )
