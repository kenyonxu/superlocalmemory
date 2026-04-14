# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Skill Evolver — orchestrates the full evolution pipeline.

Pipeline: Trigger → Screen → LLM Confirm → Mutate → Blind Verify → Persist

Performance: NEVER runs on recall/remember hot path. Only during
consolidation (every 6h) or explicit trigger. Zero impact on latency.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from superlocalmemory.evolution.types import (
    EvolutionCandidate,
    EvolutionRecord,
    EvolutionStatus,
    EvolutionType,
    TriggerType,
)
from superlocalmemory.evolution.evolution_store import EvolutionStore
from superlocalmemory.evolution.triggers import (
    DegradationTrigger,
    HealthCheckTrigger,
    PostSessionTrigger,
)
from superlocalmemory.evolution import mutation_generator as mutgen
from superlocalmemory.evolution import blind_verifier as verifier

logger = logging.getLogger(__name__)

EVOLVED_SKILLS_DIR = Path.home() / ".claude" / "skills" / "evolved"


def detect_backend() -> str:
    """Auto-detect best available LLM backend.

    Priority: claude CLI → Ollama → API key → none
    """
    import shutil

    # 1. Claude CLI available?
    if shutil.which("claude"):
        return "claude"

    # 2. Ollama running?
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return "ollama"
    except Exception:
        pass

    # 3. API key set?
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"

    return "none"


class SkillEvolver:
    """Main orchestrator for skill evolution.

    Call `run_consolidation_cycle()` from the consolidation pipeline.
    Call `run_post_session()` from the Stop hook.

    Respects EvolutionConfig.enabled — does nothing if disabled.
    Auto-detects LLM backend: claude CLI → Ollama → API → none.
    """

    def __init__(self, db_path: str | Path, config: object | None = None):
        self._db_path = str(db_path)
        self._store = EvolutionStore(db_path)
        self._degradation = DegradationTrigger(db_path)
        self._health = HealthCheckTrigger(db_path)
        self._config = config
        self._backend: str | None = None

    def _is_enabled(self) -> bool:
        """Check if evolution is enabled in config."""
        if self._config and hasattr(self._config, "evolution"):
            return self._config.evolution.enabled
        return False

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

    def run_consolidation_cycle(self, profile_id: str = "default") -> dict:
        """Run during consolidation. Checks triggers 2 and 3."""
        if not self._is_enabled():
            return {"enabled": False, "message": "Evolution disabled. Enable via: slm config set evolution.enabled true"}

        backend = self._get_backend()
        if backend == "none":
            return {"enabled": True, "backend": "none",
                    "message": "No LLM backend available. Install Claude Code, Ollama, or set an API key."}

        self._store.reset_cycle()
        results = {"candidates": 0, "evolved": 0, "rejected": 0, "skipped": 0, "backend": backend}

        # Prune recovered skills from anti-loop tracking
        active_degraded = self._degradation.get_active_degraded(profile_id)
        self._store.prune_recovered(active_degraded)

        # Trigger 2: Degradation
        candidates = self._degradation.scan(profile_id)
        # Trigger 3: Health check (runs every Nth cycle)
        candidates.extend(self._health.scan(profile_id))

        results["candidates"] = len(candidates)

        for candidate in candidates:
            if not self._store.can_evolve():
                results["skipped"] += len(candidates) - results["evolved"] - results["rejected"]
                break

            outcome = self._process_candidate(candidate, profile_id)
            if outcome == "evolved":
                results["evolved"] += 1
            elif outcome == "rejected":
                results["rejected"] += 1
            else:
                results["skipped"] += 1

        logger.info(
            "Evolution cycle: %d candidates, %d evolved, %d rejected, %d skipped",
            results["candidates"], results["evolved"],
            results["rejected"], results["skipped"],
        )
        return results

    def run_post_session(
        self, session_id: str, profile_id: str = "default",
    ) -> dict:
        """Run after a session ends. Checks trigger 1."""
        if not self._is_enabled():
            return {"enabled": False, "candidates": 0, "evolved": 0, "rejected": 0}

        results = {"candidates": 0, "evolved": 0, "rejected": 0}

        trigger = PostSessionTrigger(self._db_path)
        candidates = trigger.scan(session_id, profile_id)
        results["candidates"] = len(candidates)

        for candidate in candidates:
            if not self._store.can_evolve():
                break
            outcome = self._process_candidate(candidate, profile_id)
            if outcome == "evolved":
                results["evolved"] += 1
            elif outcome == "rejected":
                results["rejected"] += 1

        return results

    def _process_candidate(
        self, candidate: EvolutionCandidate, profile_id: str,
    ) -> str:
        """Process a single evolution candidate through the full pipeline.

        Returns: "evolved", "rejected", or "skipped"
        """
        now = datetime.now(timezone.utc).isoformat()
        record_id = hashlib.sha256(
            f"{candidate.skill_name}:{candidate.trigger.value}:{now}".encode(),
        ).hexdigest()[:16]

        # Anti-loop: check if already addressed
        context_hash = hashlib.sha256(
            json.dumps(list(candidate.evidence)).encode(),
        ).hexdigest()[:12]

        if self._store.is_addressed(candidate.skill_name, context_hash):
            return "skipped"

        if self._store.has_exceeded_attempts(candidate.skill_name):
            logger.info("Skill %s exceeded max attempts, flagging for review", candidate.skill_name)
            return "skipped"

        # Mark as addressed (even if we reject — prevents repeated checks)
        self._store.mark_addressed(candidate.skill_name, context_hash)

        # Step 1: Read original skill content
        original_content = self._read_skill_content(candidate.skill_name)

        # Create initial record
        record = EvolutionRecord(
            id=record_id,
            skill_name=candidate.skill_name,
            parent_skill_id=candidate.skill_name,
            evolution_type=candidate.evolution_type,
            trigger=candidate.trigger,
            status=EvolutionStatus.CANDIDATE,
            evidence=candidate.evidence,
            original_content=original_content[:2000],
            created_at=now,
        )
        self._store.save_record(record)

        # Step 2: LLM confirmation gate (uses Haiku for cost)
        confirmed = self._llm_confirm(candidate, original_content)
        if not confirmed:
            record = dataclasses.replace(
                record,
                status=EvolutionStatus.REJECTED,
                rejection_reason="LLM confirmation gate rejected",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._store.save_record(record)
            return "rejected"

        # Step 3: Generate mutation (uses Sonnet for quality)
        prompt = mutgen.build_mutation_prompt(candidate, original_content)
        evolved_content = self._generate_mutation(prompt)
        if not evolved_content:
            record = dataclasses.replace(
                record,
                status=EvolutionStatus.FAILED,
                rejection_reason="Mutation generation failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._store.save_record(record)
            return "rejected"

        # Step 4: Blind verification (uses Haiku — different model from generator)
        description = self._extract_description(evolved_content)
        v_prompt = verifier.build_verification_prompt(
            candidate.skill_name, description, evolved_content,
        )
        v_result = self._blind_verify(v_prompt)
        if not v_result.passed:
            record = dataclasses.replace(
                record,
                status=EvolutionStatus.REJECTED,
                rejection_reason=f"Blind verification failed: {v_result.reasoning}",
                evolved_content=evolved_content[:2000],
                blind_verified=False,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._store.save_record(record)
            return "rejected"

        # Step 5: Persist evolved skill
        diff = self._compute_diff(original_content, evolved_content)
        skill_path = self._write_evolved_skill(candidate, evolved_content, record_id)

        # M-GENERATION: Compute generation from parent's history
        parent_history = self._store.get_skill_history(candidate.skill_name, limit=1)
        parent_gen = (
            parent_history[0].generation
            if parent_history and parent_history[0].status == EvolutionStatus.PROMOTED
            else 0
        )

        record = dataclasses.replace(
            record,
            status=EvolutionStatus.PROMOTED,
            evolved_content=evolved_content[:2000],
            content_diff=diff[:2000],
            mutation_summary=self._summarize_diff(diff),
            blind_verified=True,
            generation=parent_gen + 1,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        self._store.save_record(record)
        self._store.record_evolution_attempt()

        logger.info(
            "Evolved skill: %s (%s via %s) → %s",
            candidate.skill_name, candidate.evolution_type.value,
            candidate.trigger.value, skill_path,
        )
        return "evolved"

    # ------------------------------------------------------------------
    # LLM calls — isolated, easy to mock in tests
    # ------------------------------------------------------------------

    def _llm_call(self, prompt: str, max_tokens: int = 500, model: str = "haiku") -> str:
        """Make an LLM call using the detected backend.

        Priority: claude CLI → Ollama → API → empty string
        The `model` parameter differentiates generator ("sonnet") from
        verifier ("haiku") calls so mutations use a stronger model.
        """
        backend = self._get_backend()

        if backend == "claude":
            return self._call_claude_cli(prompt, max_tokens, model=model)
        elif backend == "ollama":
            return self._call_ollama(prompt, max_tokens)
        elif backend in ("anthropic", "openai"):
            return self._call_api(prompt, max_tokens, backend, model=model)
        return ""

    def _call_claude_cli(self, prompt: str, max_tokens: int, model: str = "haiku") -> str:
        """Spawn `claude --model <model>` for a single completion (ECC pattern)."""
        import subprocess
        import tempfile

        # Write prompt to temp file (avoids shell escaping issues)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            result = subprocess.run(
                ["claude", "--model", model, "--print", "--no-input",
                 "--max-tokens", str(max_tokens),
                 "--prompt-file", prompt_file],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "cli",
                     "ECC_SKIP_OBSERVE": "1"},  # Don't observe our own evolution calls
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.debug("Claude CLI call failed: %s", exc)
            return ""
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass

    def _call_ollama(self, prompt: str, max_tokens: int) -> str:
        """Call Ollama API for local LLM completion."""
        import urllib.request
        import json as _json

        payload = _json.dumps({
            "model": "llama3",
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }).encode()

        try:
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read())
                return data.get("response", "")
        except Exception as exc:
            logger.debug("Ollama call failed: %s", exc)
            return ""

    def _call_api(self, prompt: str, max_tokens: int, provider: str, model: str = "haiku") -> str:
        """Call Anthropic or OpenAI API directly."""
        try:
            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic()
                api_model = "claude-sonnet-4-6-20250514" if model == "sonnet" else "claude-haiku-4-5-20251001"
                msg = client.messages.create(
                    model=api_model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text if msg.content else ""
            elif provider == "openai":
                import openai
                client = openai.OpenAI()
                api_model = "gpt-4o" if model == "sonnet" else "gpt-4o-mini"
                resp = client.chat.completions.create(
                    model=api_model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content or ""
        except Exception as exc:
            logger.debug("API call failed (%s): %s", provider, exc)
            return ""
        return ""  # Safety net: unmatched provider returns empty string

    def _llm_confirm(self, candidate: EvolutionCandidate, original: str) -> bool:
        """LLM confirmation gate."""
        prompt = (
            f"A skill '{candidate.skill_name}' has effective score "
            f"{candidate.effective_score:.0%} over {candidate.invocation_count} invocations.\n"
            f"Evidence: {'; '.join(candidate.evidence)}\n\n"
            f"Should this skill be evolved ({candidate.evolution_type.value})? "
            f"Reply YES or NO with brief reason."
        )
        response = self._llm_call(prompt, max_tokens=100)
        if not response:
            logger.warning("LLM confirmation gate: empty response, skipping evolution for %s", candidate.skill_name)
            return False  # Fail-closed: no LLM = no evolution
        return "yes" in response.lower()

    def _generate_mutation(self, prompt: str) -> Optional[str]:
        """Generate evolved SKILL.md (uses sonnet for quality)."""
        for attempt in range(mutgen.MAX_APPLY_RETRIES):
            response = self._llm_call(prompt, max_tokens=4000, model="sonnet")
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
                    prompt, "No valid SKILL.md content found in output", attempt + 1,
                )
        return None

    def _blind_verify(self, prompt: str) -> verifier.VerificationResult:
        """Blind verification."""
        response = self._llm_call(prompt, max_tokens=500)
        if not response:
            return verifier.VerificationResult(
                passed=False, confidence=0.0, reasoning="No LLM response",
            )
        return verifier.parse_verification_response(response)

    # ------------------------------------------------------------------
    # Skill I/O
    # ------------------------------------------------------------------

    def _read_skill_content(self, skill_name: str) -> str:
        """Read a skill's SKILL.md content. Searches known skill directories."""
        search_dirs = [
            Path.home() / ".claude" / "skills",
            Path.home() / ".claude" / "plugins",
            EVOLVED_SKILLS_DIR,
        ]

        # Handle namespaced skills (e.g., "superpowers:brainstorming")
        if ":" in skill_name:
            parts = skill_name.split(":")
            search_patterns = [
                f"**/{parts[-1]}/SKILL.md",
                f"**/{skill_name.replace(':', '/')}/SKILL.md",
                f"**/skills/{parts[-1]}/SKILL.md",
            ]
        else:
            search_patterns = [f"**/{skill_name}/SKILL.md"]

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for pattern in search_patterns:
                matches = list(search_dir.glob(pattern))
                if matches:
                    try:
                        return matches[0].read_text(encoding="utf-8")
                    except OSError:
                        continue

        return ""

    def _write_evolved_skill(
        self,
        candidate: EvolutionCandidate,
        content: str,
        record_id: str,
    ) -> Path:
        """Write evolved SKILL.md to ~/.claude/skills/evolved/."""
        EVOLVED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

        # Build directory name
        base_name = candidate.skill_name.replace(":", "-")
        if candidate.evolution_type == EvolutionType.FIX:
            dir_name = f"{base_name}-v{record_id[:6]}"
        elif candidate.evolution_type == EvolutionType.DERIVED:
            # Extract name from evolved content frontmatter
            name_match = re.search(r"name:\s*(.+)", content)
            dir_name = name_match.group(1).strip() if name_match else f"{base_name}-derived"
            dir_name = re.sub(r"[^a-zA-Z0-9_-]", "-", dir_name).lower()[:50]
        else:
            dir_name = base_name

        skill_dir = EVOLVED_SKILLS_DIR / dir_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")

        # Write metadata sidecar
        meta = {
            "skill_id": dir_name,
            "parent_skill_id": candidate.skill_name,
            "evolution_type": candidate.evolution_type.value,
            "trigger": candidate.trigger.value,
            "record_id": record_id,
            "evidence": list(candidate.evidence),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = skill_dir / ".skill_meta.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        return skill_path

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _extract_description(self, content: str) -> str:
        """Extract description from SKILL.md frontmatter."""
        match = re.search(r"description:\s*(.+?)(?:\n|---)", content)
        return match.group(1).strip() if match else "Skill for AI agent tasks"

    def _compute_diff(self, original: str, evolved: str) -> str:
        if not original:
            return "(new skill — no original to diff)"
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            evolved.splitlines(keepends=True),
            fromfile="original",
            tofile="evolved",
            n=3,
        )
        return "".join(diff)

    def _summarize_diff(self, diff: str) -> str:
        """Count additions and removals."""
        additions = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removals = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        return f"+{additions}/-{removals} lines"
