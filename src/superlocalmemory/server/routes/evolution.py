# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Evolution API routes — dashboard endpoints for skill evolution engine.

Routes: /api/evolution/status, /api/evolution/enable, /api/evolution/run
"""

import logging
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from superlocalmemory.server.config_file import read_config, update_config
from superlocalmemory.storage.read_connection import ReadConnectionFactory

from .helpers import MEMORY_DIR, get_active_profile

logger = logging.getLogger("superlocalmemory.routes.evolution")
router = APIRouter()


def _require_read(request: Request) -> None:
    """Guard evolution telemetry with READ on the active profile."""
    from superlocalmemory.access.rbac import Permission
    from superlocalmemory.server.rbac_enforce import require_permission

    require_permission(request, Permission.READ, profile=get_active_profile())


def _require_manage(request: Request) -> None:
    """Guard evolution mutations with the same RBAC boundary as v3 settings."""
    from superlocalmemory.server.rbac_enforce import require_manage

    require_manage(request)


def _read_evolution_config() -> dict:
    """Read one process-safe evolution config snapshot."""
    return dict(read_config(MEMORY_DIR / "config.json").get("evolution", {}))


def _update_evolution_config(update) -> dict:
    """Atomically update evolution without losing other config sections."""
    config_path = MEMORY_DIR / "config.json"

    def mutate(cfg: dict) -> None:
        evolution = cfg.setdefault("evolution", {})
        update(evolution)

    cfg = update_config(config_path, mutate)
    return dict(cfg.get("evolution", {}))


def _enable_evolution(config: dict) -> None:
    config["enabled"] = True
    config.setdefault("backend", "auto")


@router.get("/api/evolution/status")
def evolution_status(request: Request):
    """Get evolution engine status, backend, and recent history."""
    _require_read(request)
    try:
        from superlocalmemory.evolution.evolution_store import EvolutionStore
        from superlocalmemory.evolution.skill_evolver import detect_backend

        evo_cfg = _read_evolution_config()
        enabled = evo_cfg.get("enabled", False)
        backend_setting = evo_cfg.get("backend", "auto")
        backend = (
            detect_backend() if enabled and backend_setting == "auto"
            else backend_setting if enabled
            else "none"
        )
        db_path = str(MEMORY_DIR / "memory.db")

        profile_id = get_active_profile()
        store = EvolutionStore(db_path)
        stats = store.get_stats(profile_id)
        recent = store.get_recent(profile_id, limit=10)

        return {
            "enabled": enabled,
            "backend": backend,
            "config": {
                "backend_setting": backend_setting,
                "max_per_cycle": evo_cfg.get("max_evolutions_per_cycle", 3),
                "mutation_model": evo_cfg.get("mutation_model", ""),
                "verify_model": evo_cfg.get("verify_model", ""),
                "confirm_model": evo_cfg.get("confirm_model", ""),
            },
            "stats": {
                "total": stats.get("total", 0),
                "promoted": stats.get("by_status", {}).get("promoted", 0),
                "rejected": stats.get("by_status", {}).get("rejected", 0),
                "failed": stats.get("by_status", {}).get("failed", 0),
                "cycle_budget_remaining": stats.get("cycle_budget_remaining", 3),
            },
            "recent": [
                {
                    "id": r.id,
                    "skill_name": r.skill_name,
                    "evolution_type": r.evolution_type.value,
                    "trigger": r.trigger.value,
                    "status": r.status.value,
                    "mutation_summary": r.mutation_summary,
                    "blind_verified": r.blind_verified,
                    "created_at": r.created_at,
                }
                for r in recent
            ],
        }
    except Exception:
        logger.exception("evolution_status error")
        return {"enabled": False, "backend": "none", "error": "Internal server error"}


@router.post("/api/evolution/enable")
def evolution_enable(request: Request):
    """Enable evolution without replacing the user's selected backend."""
    _require_manage(request)
    try:
        evolution = _update_evolution_config(_enable_evolution)
        return {
            "ok": True,
            "message": f"Evolution enabled with {evolution['backend']} backend.",
        }
    except Exception:
        logger.exception("evolution_enable error")
        return {"ok": False, "error": "Internal server error"}


@router.post("/api/evolution/disable")
def evolution_disable(request: Request):
    """Disable skill evolution engine.  Mirrors /api/evolution/enable."""
    _require_manage(request)
    try:
        _update_evolution_config(lambda cfg: cfg.update({"enabled": False}))

        return {"ok": True, "message": "Evolution disabled."}
    except Exception:
        logger.exception("evolution_disable error")
        return {"ok": False, "error": "Internal server error"}


@router.post("/api/evolution/run")
def evolution_run(request: Request):
    """Manually trigger an evolution cycle."""
    _require_manage(request)
    try:
        from superlocalmemory.evolution.skill_evolver import SkillEvolver

        evo_cfg = _read_evolution_config()
        if not evo_cfg.get("enabled", False):
            return {"ok": False, "error": "Evolution is disabled. Enable first."}

        profile = get_active_profile()
        db_path = str(MEMORY_DIR / "memory.db")

        # Build a minimal config object for the evolver. Must carry the
        # per-step model fields (v3.7.9) or a dashboard-triggered run would
        # silently ignore the user's configured models and fall back to the
        # cheapest defaults.
        evolution_config = SimpleNamespace(
            enabled=True,
            backend=evo_cfg.get("backend", "auto"),
            max_evolutions_per_cycle=evo_cfg.get("max_evolutions_per_cycle", 3),
            mutation_model=evo_cfg.get("mutation_model", ""),
            verify_model=evo_cfg.get("verify_model", ""),
            confirm_model=evo_cfg.get("confirm_model", ""),
        )
        evolver = SkillEvolver(
            db_path, SimpleNamespace(evolution=evolution_config)
        )
        result = evolver.run_consolidation_cycle(profile)

        return {"ok": True, **result}
    except Exception:
        logger.exception("evolution_run error")
        return {"ok": False, "error": "Internal server error"}


class EvolutionConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    backend: Optional[str] = None
    max_evolutions_per_cycle: Optional[int] = None
    mutation_model: Optional[str] = None
    verify_model: Optional[str] = None
    confirm_model: Optional[str] = None


@router.post("/api/evolution/config")
def evolution_config(request: Request, body: EvolutionConfigUpdate):
    """Update evolution config from the dashboard (v3.7.9).

    Validates model + backend values against the same allow-list the CLI uses
    (``slm config set evolution.*``) and persists to config.json atomically.
    Only fields provided in the body are changed.
    """
    _require_manage(request)
    try:
        from superlocalmemory.evolution.model_selection import _MODEL_ALIASES

        accepted_models = set(_MODEL_ALIASES) | {"", "auto"}
        accepted_backends = {"auto", "claude", "ollama", "anthropic", "openai"}

        for field in ("mutation_model", "verify_model", "confirm_model"):
            val = getattr(body, field)
            if val is not None and val not in accepted_models:
                allowed = ", ".join(["auto", *sorted(_MODEL_ALIASES)])
                return {"ok": False, "error": f"{field} must be one of: {allowed}"}
        if body.backend is not None and body.backend not in accepted_backends:
            return {
                "ok": False,
                "error": f"backend must be one of: {', '.join(sorted(accepted_backends))}",
            }
        if (body.max_evolutions_per_cycle is not None
                and not 0 < body.max_evolutions_per_cycle <= 50):
            return {"ok": False, "error": "max_evolutions_per_cycle must be 1..50"}

        def _apply(evo: dict) -> None:
            for field in (
                "enabled",
                "backend",
                "max_evolutions_per_cycle",
                "mutation_model",
                "verify_model",
                "confirm_model",
            ):
                value = getattr(body, field)
                if value is None:
                    continue
                if field.endswith("_model") and value == "auto":
                    value = ""
                evo[field] = value

        evo = _update_evolution_config(_apply)

        return {"ok": True, "config": {
            "enabled": evo.get("enabled", False),
            "backend": evo.get("backend", "auto"),
            "max_evolutions_per_cycle": evo.get("max_evolutions_per_cycle", 3),
            "mutation_model": evo.get("mutation_model", ""),
            "verify_model": evo.get("verify_model", ""),
            "confirm_model": evo.get("confirm_model", ""),
        }}
    except Exception:
        logger.exception("evolution_config error")
        return {"ok": False, "error": "Internal server error"}


@router.get("/api/evolution/lineage")
def evolution_lineage(request: Request, skill_name: str = ""):
    """Get evolution lineage for a skill or all skills.

    Returns lineage records and a tree structure grouped by root skill.
    """
    _require_read(request)
    conn = None
    try:
        db_path = MEMORY_DIR / "memory.db"
        profile_id = get_active_profile()
        conn = ReadConnectionFactory(db_path).open()

        if skill_name:
            rows = conn.execute(
                "SELECT id, skill_name, parent_skill_id, evolution_type, "
                "trigger_type, generation, status, mutation_summary, "
                "blind_verified, created_at, completed_at "
                "FROM skill_evolution_log "
                "WHERE profile_id = ? AND (skill_name = ? OR parent_skill_id = ?) "
                "ORDER BY created_at ASC",
                (profile_id, skill_name, skill_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, skill_name, parent_skill_id, evolution_type, "
                "trigger_type, generation, status, mutation_summary, "
                "blind_verified, created_at, completed_at "
                "FROM skill_evolution_log "
                "WHERE profile_id = ? "
                "ORDER BY created_at DESC LIMIT 100",
                (profile_id,),
            ).fetchall()

        lineage = [
            {
                "id": dict(r)["id"],
                "skill_name": dict(r)["skill_name"],
                "parent_skill_id": dict(r).get("parent_skill_id", ""),
                "evolution_type": dict(r)["evolution_type"],
                "trigger": dict(r)["trigger_type"],
                "generation": dict(r).get("generation", 0),
                "status": dict(r)["status"],
                "mutation_summary": dict(r).get("mutation_summary", ""),
                "blind_verified": bool(dict(r).get("blind_verified", 0)),
                "created_at": dict(r).get("created_at", ""),
                "completed_at": dict(r).get("completed_at", ""),
            }
            for r in rows
        ]

        # Build tree structure: group by root skill
        tree: dict = {}
        for entry in lineage:
            root = entry.get("parent_skill_id") or entry["skill_name"]
            if root not in tree:
                tree[root] = {"root": root, "evolutions": []}
            tree[root]["evolutions"].append({
                "id": entry["id"],
                "skill_name": entry["skill_name"],
                "evolution_type": entry["evolution_type"],
                "status": entry["status"],
                "generation": entry["generation"],
                "created_at": entry["created_at"],
            })

        return {
            "lineage": lineage,
            "lineage_count": len(lineage),
            "tree": tree,
        }
    except Exception:
        logger.exception("evolution_lineage error")
        return {"lineage": [], "lineage_count": 0, "tree": {}, "error": "Internal server error"}
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Approving a quarantined skill
#
# A mutation that passes blind verification stops at VERIFIED_QUARANTINED and
# waits. Nothing moved it from there: auto-approval is off by default, correctly
# — this system rewrites the instructions an AI follows, and doing that without
# a person saying yes is not a default anyone should ship. But there was no way
# for the person to say yes either, so every verified improvement sat in a
# quarantine directory permanently.
#
# This is that path. It does NOT change the default: approval remains explicit,
# per-candidate, and recorded in the transition chain with who did it.
# ---------------------------------------------------------------------------


class ApproveSkillRequest(BaseModel):
    """Which candidate to approve. Named by record id, not by skill name.

    A skill can have several candidates over time and only one of them is the
    one being looked at. Approving "the latest candidate for skill X" would make
    the outcome depend on when the call happened to arrive.
    """

    record_id: str


_APPROVABLE = ("verified_quarantined", "promoted")


@router.post("/api/evolution/approve")
def evolution_approve(request: Request, body: ApproveSkillRequest):
    """Activate a quarantined skill mutation after human approval."""
    _require_manage(request)
    try:
        from superlocalmemory.evolution.evolution_store import EvolutionStore
        from superlocalmemory.evolution.skill_activator import SkillActivator
        from superlocalmemory.evolution.types import EvolutionStatus

        profile_id = get_active_profile()
        store = EvolutionStore(str(MEMORY_DIR / "memory.db"))

        record = store.get_record(body.record_id, profile_id)
        if record is None:
            return {"success": False, "error": "No such evolution candidate"}

        # Prefer the transition chain over the record's own column: the chain is
        # the audited history and the column is a cache of its last entry.
        latest = store.get_latest_status(body.record_id, profile_id)
        current = (latest.value if latest is not None
                   else getattr(record.status, "value", ""))

        if current not in _APPROVABLE:
            # Refusing an already-active candidate matters: activating twice
            # would overwrite the backup taken the first time with the mutation
            # itself, and the rollback target would become the thing being
            # rolled back.
            return {
                "success": False,
                "error": (
                    f"Candidate is {current!r}; only a verified candidate "
                    "awaiting approval can be activated"
                ),
                "status": current,
            }

        if not record.quarantine_dir_name:
            return {
                "success": False,
                "error": "Candidate has no quarantined artifact to activate",
            }

        actor = "dashboard"

        # Approval is recorded BEFORE the file moves, activation after. The
        # order matters in both directions:
        #
        # * recording both afterwards means a successful activation whose log
        #   write fails leaves the skill live and the record saying it is not —
        #   so the next approval attempt is allowed, and activating twice
        #   overwrites the backup that rollback restores from.
        # * recording both beforehand would claim a mutation is live when the
        #   file move failed.
        #
        # Approved-then-nothing is a state a person can act on. Live-but-unknown
        # is not.
        store.append_transition(
            body.record_id, profile_id,
            EvolutionStatus(current), EvolutionStatus.APPROVED,
            actor_id=actor, reason="approved by request",
        )
        try:
            activation = SkillActivator().activate(
                record.skill_name, record.quarantine_dir_name, actor_id=actor,
            )
        except Exception as exc:
            # The approval stands and the mutation did not go live. Recorded as
            # such rather than left dangling.
            store.append_transition(
                body.record_id, profile_id,
                EvolutionStatus.APPROVED, EvolutionStatus.REJECTED,
                actor_id=actor, reason=f"activation failed: {exc}",
            )
            raise
        store.append_transition(
            body.record_id, profile_id,
            EvolutionStatus.APPROVED, EvolutionStatus.ACTIVE,
            actor_id=actor, reason="activated from quarantine",
            metadata={"content_hash": activation.get("content_hash", "")},
        )

        return {
            "success": True,
            "record_id": body.record_id,
            "skill_name": record.skill_name,
            "status": EvolutionStatus.ACTIVE.value,
            "live_path": activation.get("live_path"),
            "backup_path": activation.get("backup_path"),
            "content_hash": activation.get("content_hash"),
            # So the caller knows how to undo it without reading the source.
            "rollback": "POST /api/evolution/rollback",
        }
    except FileNotFoundError as exc:
        return {"success": False, "error": f"Quarantined artifact missing: {exc}"}
    except Exception:
        logger.exception("evolution_approve error")
        return {"success": False, "error": "Internal server error"}


class RollbackSkillRequest(BaseModel):
    skill_name: str
    #: Optional, so the reversal can be recorded against the candidate it
    #: reverses. Without it the file is restored and the log still says active,
    #: which reads as "this mutation is live" forever.
    record_id: str = ""


@router.post("/api/evolution/rollback")
def evolution_rollback(request: Request, body: RollbackSkillRequest):
    """Restore a skill's previous instructions after an approval goes wrong.

    Approval is reversible, and it has to be: the reason a person is in this
    loop is that a verified mutation can still be a bad one, and finding that
    out happens after it is live.
    """
    _require_manage(request)
    try:
        from superlocalmemory.evolution.evolution_store import EvolutionStore
        from superlocalmemory.evolution.skill_activator import SkillActivator
        from superlocalmemory.evolution.types import EvolutionStatus

        result = SkillActivator().rollback(body.skill_name)

        # Record the reversal. A restored file with the log still reading
        # "active" says the mutation is live when it is not, and that is the
        # record an audit would read.
        if body.record_id:
            try:
                store = EvolutionStore(str(MEMORY_DIR / "memory.db"))
                pid = get_active_profile()
                latest = store.get_latest_status(body.record_id, pid)
                store.append_transition(
                    body.record_id, pid,
                    latest or EvolutionStatus.ACTIVE,
                    EvolutionStatus.ROLLED_BACK,
                    actor_id="dashboard", reason="rolled back by request",
                )
            except Exception as exc:  # pragma: no cover — the file is restored
                logger.warning("rollback recorded no transition: %s", exc)
                result["transition_recorded"] = False

        return {"success": True, **result}
    except FileNotFoundError as exc:
        return {"success": False, "error": f"No backup to restore: {exc}"}
    except Exception:
        logger.exception("evolution_rollback error")
        return {"success": False, "error": "Internal server error"}
