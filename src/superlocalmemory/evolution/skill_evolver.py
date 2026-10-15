# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Skill Evolver — orchestrates the full evolution pipeline.

Pipeline: Trigger → Screen → LLM Confirm → Mutate → Blind Verify → Persist

Performance: NEVER runs on recall/remember hot path. Only during
consolidation (every 6h) or explicit trigger. Zero impact on latency.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from superlocalmemory.evolution import blind_verifier as verifier
from superlocalmemory.evolution import mutation_generator as mutgen
from superlocalmemory.evolution.budget import (
    BudgetExhausted,
    EvolutionBudget,
)
from superlocalmemory.evolution.evolution_store import EvolutionStore
from superlocalmemory.evolution.llm_dispatch import _dispatch_llm
from superlocalmemory.evolution.model_selection import (
    _MODEL_ALIASES,
    _resolve_model_alias,
    resolve_evolution_models,
)
from superlocalmemory.evolution.skill_artifacts import SkillArtifacts
from superlocalmemory.evolution.triggers import (
    DegradationTrigger,
    HealthCheckTrigger,
    PostSessionTrigger,
)
from superlocalmemory.evolution.types import (
    EvolutionCandidate,
)
from superlocalmemory.infra.data_root import canonical_data_root

logger = logging.getLogger(__name__)

EVOLVED_SKILLS_DIR = canonical_data_root() / "quarantine" / "skills"

__all__ = ["SkillEvolver", "detect_backend", "_resolve_model_alias", "_MODEL_ALIASES"]


def _ollama_running() -> bool:
    """Return True if a local Ollama daemon answers on the default port."""
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2):  # noqa: S310
            return True
    except Exception:
        return False


def _ollama_models() -> tuple[str, ...]:
    """Discover installed models only; never pull or alter the shared server."""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            data = json.load(response)
        names = tuple(item["name"] for item in data.get("models", []) if "name" in item)
        preferred = "llama3.2:latest"
        return tuple(sorted(names, key=lambda name: (name != preferred, name)))
    except (OSError, ValueError, KeyError, TypeError):
        return ()


def detect_backend() -> str:
    """Auto-detect best available LLM backend.

    Priority: claude CLI → Ollama → API key → none
    """
    import shutil

    if shutil.which("claude"):
        return "claude"

    if _ollama_running():
        return "ollama"

    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"

    return "none"


class SkillEvolver(SkillArtifacts):
    """Main orchestrator for skill evolution.

    Call `run_consolidation_cycle()` from the consolidation pipeline.
    Call `run_post_session()` from the Stop hook.

    Respects EvolutionConfig.enabled — does nothing if disabled.
    Auto-detects LLM backend: claude CLI → Ollama → API → none.
    """

    def __init__(
        self,
        db_path: str | Path,
        config: object | None = None,
        *,
        profile_id: str = "default",
        budget: EvolutionBudget | None = None,
        audit_chain=None,
        activator=None,
    ):
        self._db_path = str(db_path)
        self._store = EvolutionStore(db_path)
        self._degradation = DegradationTrigger(db_path)
        self._health = HealthCheckTrigger(db_path, profile_id=profile_id)
        self._config = config
        self._backend: str | None = None
        self._models = None
        self._profile_id = profile_id
        self._current_cycle_id: str | None = None
        self._activator = activator

        if budget is None:
            slm_home = canonical_data_root()
            learning_db = Path(self._db_path)
            if learning_db.name == "memory.db":
                learning_db = learning_db.parent / "learning.db"
            budget = EvolutionBudget(
                profile_id=profile_id,
                learning_db=learning_db,
                lock_dir=slm_home,
            )
        self._budget = budget

        if audit_chain is None:
            from superlocalmemory.compliance.audit import AuditChain

            if self._db_path == ":memory:":
                audit_chain = AuditChain(":memory:")
            else:
                audit_db = Path(self._db_path).parent / "audit.db"
                audit_chain = AuditChain(audit_db)
        self._audit = audit_chain

    def _is_enabled(self) -> bool:
        """Check if evolution is enabled in config."""
        if self._config and hasattr(self._config, "evolution"):
            return self._config.evolution.enabled
        return False

    def _is_auto_approve(self) -> bool:
        """Check evolution.auto_approve config key (LLD Decision A2, default False).

        Config key wins. Falls back to SLM_EVO_AUTO_APPROVE env var for CI.
        """
        evo_cfg = getattr(self._config, "evolution", None)
        if evo_cfg is not None and hasattr(evo_cfg, "auto_approve"):
            return bool(evo_cfg.auto_approve)
        return os.environ.get("SLM_EVO_AUTO_APPROVE", "").strip() in (
            "1",
            "true",
            "True",
        )

    def _get_activator(self):
        """Return the SkillActivator instance (injectable for tests)."""
        if self._activator is not None:
            return self._activator
        from superlocalmemory.evolution.skill_activator import SkillActivator

        return SkillActivator()

    def _get_backend(self) -> str:
        """Get or detect the LLM backend."""
        if self._backend:
            return self._backend

        configured = "auto"
        if self._config and hasattr(self._config, "evolution"):
            configured = self._config.evolution.backend

        if configured == "auto":
            self._backend = detect_backend()
        else:
            self._backend = configured

        logger.info("Evolution backend: %s", self._backend)
        return self._backend

    def _get_models(self):
        """Resolve per-step evolution models (cached).

        Defaults to the lowest-cost model for the active backend and keeps
        the blind verifier independent of the generator where possible
        (see ``model_selection.resolve_evolution_models``). Falls back to
        stock ``EvolutionConfig`` defaults when no config is attached.
        """
        if self._models is not None:
            return self._models
        backend = self._get_backend()
        evo_cfg = getattr(self._config, "evolution", None)
        if evo_cfg is None:
            from superlocalmemory.core.config import EvolutionConfig

            evo_cfg = EvolutionConfig()
        self._models = resolve_evolution_models(
            evo_cfg,
            backend,
            ollama_available=_ollama_running(),
            installed_models=_ollama_models() if backend == "ollama" else None,
        )
        return self._models

    def run_consolidation_cycle(self, profile_id: str = "default") -> dict:
        """Run during consolidation. Checks triggers 2 and 3.

        Wrapped in ``self._budget.cycle()`` — honours the
        30min/10-LLM-calls/3-cycles-per-day caps (SB-3). If the budget
        is exhausted, returns ``{"aborted": True}`` rather than raising
        so the consolidation worker can continue cleanly.
        """
        if not self._is_enabled():
            return {
                "enabled": False,
                "message": "Evolution disabled. Enable via: slm config set evolution.enabled true",
            }

        backend = self._get_backend()
        if backend == "none":
            return {
                "enabled": True,
                "backend": "none",
                "message": (
                    "No LLM backend available. Install Claude Code, Ollama, or set an API key."
                ),
            }

        try:
            with self._budget.cycle() as _b:
                self._current_cycle_id = None  # budget already recorded one
                return self._run_consolidation_body(profile_id, backend)
        except BudgetExhausted as exc:
            logger.warning(
                "evolution cycle aborted: budget exhausted [%s]",
                getattr(exc, "dimension", "?"),
            )
            return {
                "enabled": True,
                "backend": backend,
                "aborted": True,
                "budget_exhausted": True,
                "dimension": getattr(exc, "dimension", None),
                "candidates": 0,
                "evolved": 0,
                "rejected": 0,
                "skipped": 0,
            }
        finally:
            self._current_cycle_id = None

    def _run_consolidation_body(
        self,
        profile_id: str,
        backend: str,
    ) -> dict:
        """Inner consolidation loop — runs under an open budget cycle."""
        self._store.reset_cycle(profile_id)
        results = {
            "candidates": 0,
            "evolved": 0,
            "rejected": 0,
            "skipped": 0,
            "quarantined": 0,
            "backend": backend,
        }

        active_degraded = self._degradation.get_active_degraded(profile_id)
        self._store.prune_recovered(active_degraded)

        candidates = self._degradation.scan(profile_id)
        candidates.extend(self._health.scan(profile_id))

        results["candidates"] = len(candidates)

        for candidate in candidates:
            if not self._store.can_evolve(profile_id):
                results["skipped"] += len(candidates) - results["evolved"] - results["rejected"]
                break

            outcome = self._process_candidate(candidate, profile_id)
            if outcome == "evolved":
                results["evolved"] += 1
            elif outcome == "rejected":
                results["rejected"] += 1
            elif outcome == "quarantined":
                results["quarantined"] += 1
            else:
                results["skipped"] += 1

        logger.info(
            "Evolution cycle: %d candidates, %d evolved, %d rejected, %d skipped",
            results["candidates"],
            results["evolved"],
            results["rejected"],
            results["skipped"],
        )
        return results

    def run_post_session(
        self,
        session_id: str,
        profile_id: str = "default",
    ) -> dict:
        """Run after a session ends. Checks trigger 1.

        Wrapped in a budget cycle (SB-3) so post-session evolution is
        subject to the same 10-LLM-call / 30-min wall-time cap.
        """
        if not self._is_enabled():
            return {"enabled": False, "candidates": 0, "evolved": 0, "rejected": 0}

        try:
            with self._budget.cycle() as _b:
                return self._run_post_session_body(session_id, profile_id)
        except BudgetExhausted as exc:
            logger.warning(
                "post-session evolution aborted: budget exhausted [%s]",
                getattr(exc, "dimension", "?"),
            )
            return {
                "enabled": True,
                "aborted": True,
                "budget_exhausted": True,
                "dimension": getattr(exc, "dimension", None),
                "candidates": 0,
                "evolved": 0,
                "rejected": 0,
            }
        finally:
            self._current_cycle_id = None

    def _run_post_session_body(
        self,
        session_id: str,
        profile_id: str,
    ) -> dict:
        results = {"candidates": 0, "evolved": 0, "rejected": 0}

        trigger = PostSessionTrigger(self._db_path)
        candidates = trigger.scan(session_id, profile_id)
        results["candidates"] = len(candidates)

        for candidate in candidates:
            if not self._store.can_evolve(profile_id):
                break
            outcome = self._process_candidate(candidate, profile_id)
            if outcome == "evolved":
                results["evolved"] += 1
            elif outcome == "rejected":
                results["rejected"] += 1

        return results

    def evolve_candidate(
        self,
        candidate: EvolutionCandidate,
        profile_id: str = "default",
    ) -> str:
        """Evolve a single candidate under a budget cycle (audit-10 fix).

        Public entry point for the ``evolve_skill`` MCP tool. Opening the
        budget cycle HERE is what makes the per-cycle LLM-call, wall-time and
        per-day caps apply to manual/MCP-triggered evolution, exactly as they
        do to consolidation. The MCP tool previously called
        ``_process_candidate`` directly, so budget charges happened outside a
        cycle, the guard raised RuntimeError, ``_llm_call`` swallowed it, and
        every cap was silently bypassed.
        """
        try:
            with self._budget.cycle():
                self._current_cycle_id = None
                return self._process_candidate(candidate, profile_id)
        except BudgetExhausted as exc:
            logger.info(
                "evolve_candidate skipped: budget exhausted [%s]",
                getattr(exc, "dimension", "?"),
            )
            return "skipped"

    def _process_candidate(self, candidate: EvolutionCandidate, profile_id: str) -> str:
        """Keep orchestration stages separate from model and artifact adapters."""
        from superlocalmemory.evolution.candidate_pipeline import CandidatePipeline

        return CandidatePipeline(self, candidate, profile_id).run()

    def _llm_call(
        self,
        prompt: str,
        max_tokens: int = 500,
        model: str = "haiku",
    ) -> str:
        """Funnel a prompt through :func:`_dispatch_llm`.

        The ``model`` arg accepts short aliases (``haiku``/``sonnet``)
        for backward compatibility; they are resolved to the allow-listed
        model id before dispatch. Budget charge happens BEFORE dispatch;
        on ``BudgetExhausted`` we return an empty string so the caller
        treats this as "no evolution" (the usual fail-closed semantics).
        """
        backend = self._get_backend()
        if backend == "none":
            return ""

        try:
            self._budget.charge_llm_call()
            self._budget.check_time()
        except BudgetExhausted as exc:
            logger.info("evolution _llm_call skipped: %s", exc)
            return ""
        except RuntimeError:
            pass

        resolved_model = _resolve_model_alias(model)
        try:
            return _dispatch_llm(
                prompt,
                model=resolved_model,
                learning_db=Path(self._db_path),
                profile_id=self._profile_id,
                max_tokens=max_tokens,
                cycle_id=self._current_cycle_id,
            )
        except ValueError as exc:
            logger.error(
                "evolution dispatch misconfigured — no evolution this "
                "call (returning empty string): %s",
                exc,
            )
            return ""
        except Exception as exc:  # noqa: BLE001 — fail-closed
            logger.debug("evolution dispatch failed: %s", exc)
            return ""

    def _parse_approval_decision(self, response: str) -> bool:
        """Parse LLM approval decision from structured JSON.

        Accepts ONLY {"decision": "approve"} (case-insensitive value).
        Rejects: "yes", "maybe", "approve" as free-text, partial JSON, or any
        other value including {"decision": "yes"} or {"decision": "approved"}.

        LLMs sometimes emit leading text before the JSON object — the parser
        finds the first '{' and last '}' to extract the JSON fragment.

        Returns True only if decision is exactly "approve" (case-insensitive).
        Returns False for all other inputs including empty string, malformed
        JSON, or any decision value other than "approve".
        """
        if not response:
            return False
        stripped = response.strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return False
        try:
            obj = json.loads(stripped[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return False
        if not isinstance(obj, dict):
            return False
        decision = obj.get("decision", "")
        return isinstance(decision, str) and decision.lower() == "approve"

    def _build_confirm_prompt(
        self,
        candidate: EvolutionCandidate,
        original: str,
    ) -> str:
        """Build the strict-JSON LLM confirmation prompt."""
        return (
            f"A skill '{candidate.skill_name}' has effective score "
            f"{candidate.effective_score:.0%} over "
            f"{candidate.invocation_count} invocations.\n"
            f"Evidence: {'; '.join(candidate.evidence)}\n\n"
            f"Should this skill be evolved ({candidate.evolution_type.value})?\n"
            f"Respond ONLY with a JSON object. No other text.\n"
            f'Approve: {{"decision": "approve"}}\n'
            f'Reject: {{"decision": "reject", "reason": "one sentence"}}'
        )

    def _llm_confirm(self, candidate: EvolutionCandidate, original: str) -> bool:
        """LLM confirmation gate — strict structured decision parse (Module 3).

        Replaces the legacy 'yes' in response.lower() check with strict JSON
        parsing. Only {"decision": "approve"} returns True.
        """
        prompt = self._build_confirm_prompt(candidate, original)
        response = self._llm_call(
            prompt,
            max_tokens=100,
            model=self._get_models().confirm,
        )
        if not response:
            logger.warning(
                "LLM confirmation gate: empty response, skipping evolution for %s",
                candidate.skill_name,
            )
            return False  # Fail-closed: no LLM = no evolution
        approved = self._parse_approval_decision(response)
        if not approved:
            logger.info(
                "LLM confirmation gate rejected %s (response: %.80r)",
                candidate.skill_name,
                response,
            )
        return approved

    def _generate_mutation(self, prompt: str) -> Optional[str]:
        """Generate evolved SKILL.md via the configured mutation model.

        Defaults to the lowest-cost model for the backend (v3.7.9);
        users can opt up (e.g. to sonnet) via ``evolution.mutation_model``.
        """
        mutation_model = self._get_models().mutation
        for attempt in range(mutgen.MAX_APPLY_RETRIES):
            response = self._llm_call(
                prompt,
                max_tokens=4000,
                model=mutation_model,
            )
            if not response:
                return None
            content = mutgen.parse_mutation_output(response)
            if content:
                error = mutgen.validate_skill_content(content)
                if error is None:
                    return content
                prompt = mutgen.build_retry_prompt(prompt, error, attempt + 1)
            else:
                prompt = mutgen.build_retry_prompt(
                    prompt,
                    "No valid SKILL.md content found in output",
                    attempt + 1,
                )
        return None

    def _blind_verify(self, prompt: str) -> verifier.VerificationResult:
        """Blind verification — runs on a model independent of the generator.

        See ``model_selection.resolve_evolution_models``: the verify model
        is kept different from the mutation model where possible so the
        generator can't grade its own homework.
        """
        response = self._llm_call(
            prompt,
            max_tokens=500,
            model=self._get_models().verify,
        )
        if not response:
            return verifier.VerificationResult(
                passed=False,
                confidence=0.0,
                reasoning="No LLM response",
            )
        return verifier.parse_verification_response(response)
