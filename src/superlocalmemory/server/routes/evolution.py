# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Evolution API routes — dashboard endpoints for skill evolution engine.

Routes: /api/evolution/status, /api/evolution/enable, /api/evolution/run
"""

import logging
from pathlib import Path

from fastapi import APIRouter

from .helpers import get_active_profile, MEMORY_DIR

logger = logging.getLogger("superlocalmemory.routes.evolution")
router = APIRouter()


@router.get("/api/evolution/status")
async def evolution_status():
    """Get evolution engine status, backend, and recent history."""
    try:
        import json as _json
        from superlocalmemory.evolution.skill_evolver import detect_backend
        from superlocalmemory.evolution.evolution_store import EvolutionStore

        # Read config directly from config.json (SLMConfig.load doesn't serialize evolution)
        config_path = MEMORY_DIR / "config.json"
        evo_cfg = {}
        if config_path.exists():
            with open(config_path) as f:
                cfg = _json.load(f)
            evo_cfg = cfg.get("evolution", {})

        enabled = evo_cfg.get("enabled", False)
        backend = detect_backend() if enabled else "none"
        db_path = str(MEMORY_DIR / "memory.db")

        store = EvolutionStore(db_path)
        stats = store.get_stats()
        recent = store.get_recent(limit=10)

        return {
            "enabled": enabled,
            "backend": backend,
            "config": {
                "backend_setting": evo_cfg.get("backend", "auto"),
                "max_per_cycle": evo_cfg.get("max_evolutions_per_cycle", 3),
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
    except Exception as e:
        logger.debug("evolution_status error: %s", e)
        return {"enabled": False, "backend": "none", "error": str(e)}


@router.post("/api/evolution/enable")
async def evolution_enable():
    """Enable skill evolution engine. Writes directly to config.json."""
    try:
        import json as _json

        config_path = MEMORY_DIR / "config.json"
        cfg = {}
        if config_path.exists():
            with open(config_path) as f:
                cfg = _json.load(f)

        if "evolution" not in cfg:
            cfg["evolution"] = {}
        cfg["evolution"]["enabled"] = True
        cfg["evolution"]["backend"] = "auto"

        with open(config_path, "w") as f:
            _json.dump(cfg, f, indent=2)

        return {"ok": True, "message": "Evolution enabled. Will use auto-detected backend."}
    except Exception as e:
        logger.error("evolution_enable error: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/api/evolution/run")
async def evolution_run():
    """Manually trigger an evolution cycle."""
    try:
        import json as _json
        from superlocalmemory.evolution.skill_evolver import SkillEvolver

        config_path = MEMORY_DIR / "config.json"
        evo_cfg = {}
        if config_path.exists():
            with open(config_path) as f:
                evo_cfg = _json.load(f).get("evolution", {})

        if not evo_cfg.get("enabled", False):
            return {"ok": False, "error": "Evolution is disabled. Enable first."}

        profile = get_active_profile()
        db_path = str(MEMORY_DIR / "memory.db")

        # Build a minimal config object for the evolver
        class _EvoCfg:
            enabled = True
            backend = evo_cfg.get("backend", "auto")
            max_evolutions_per_cycle = evo_cfg.get("max_evolutions_per_cycle", 3)
        class _Cfg:
            evolution = _EvoCfg()

        evolver = SkillEvolver(db_path, _Cfg())
        result = evolver.run_consolidation_cycle(profile)

        return {"ok": True, **result}
    except Exception as e:
        logger.error("evolution_run error: %s", e)
        return {"ok": False, "error": str(e)}


@router.get("/api/evolution/lineage")
async def evolution_lineage(skill_name: str = ""):
    """Get evolution lineage for a skill or all skills.

    Returns lineage records and a tree structure grouped by root skill.
    """
    try:
        import sqlite3 as _sqlite3

        db_path = str(MEMORY_DIR / "memory.db")
        conn = _sqlite3.connect(db_path, timeout=10)
        conn.row_factory = _sqlite3.Row

        if skill_name:
            rows = conn.execute(
                "SELECT id, skill_name, parent_skill_id, evolution_type, "
                "trigger_type, generation, status, mutation_summary, "
                "blind_verified, created_at, completed_at "
                "FROM skill_evolution_log "
                "WHERE skill_name = ? OR parent_skill_id = ? "
                "ORDER BY created_at ASC",
                (skill_name, skill_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, skill_name, parent_skill_id, evolution_type, "
                "trigger_type, generation, status, mutation_summary, "
                "blind_verified, created_at, completed_at "
                "FROM skill_evolution_log "
                "ORDER BY created_at DESC LIMIT 100",
            ).fetchall()

        conn.close()

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
    except Exception as e:
        logger.debug("evolution_lineage error: %s", e)
        return {"lineage": [], "lineage_count": 0, "tree": {}, "error": str(e)}
