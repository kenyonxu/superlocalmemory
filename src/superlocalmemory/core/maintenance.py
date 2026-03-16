# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Background Math Maintenance.

Periodic batch processing for mathematical layers:
1. Langevin batch_step on all active facts (self-organization)
2. Sheaf batch consistency check on recent facts
3. Fisher adaptive temperature recalculation

Frequency: every 6-24h or after 100 stores.
~100 Langevin steps to stationarity.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: MIT
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.database import DatabaseManager

logger = logging.getLogger(__name__)


def run_maintenance(
    db: DatabaseManager,
    config: SLMConfig,
    profile_id: str = "default",
) -> dict[str, int]:
    """Run background maintenance on mathematical layers.

    Args:
        db: Database manager.
        config: Full SLM configuration.
        profile_id: Scope to this profile.

    Returns:
        Dict of counts: langevin_updated, sheaf_checked, etc.
    """
    counts: dict[str, int] = {
        "langevin_updated": 0,
        "fisher_coupled": 0,
        "sheaf_checked": 0,
    }

    facts = db.get_all_facts(profile_id)
    if not facts:
        return counts

    # 1. Langevin batch step
    if config.math.langevin_persist_positions:
        try:
            from superlocalmemory.math.langevin import LangevinDynamics

            ld = LangevinDynamics(
                dim=8,
                dt=config.math.langevin_dt,
                temperature=config.math.langevin_temperature,
            )
            fact_dicts = []
            for f in facts:
                if f.langevin_position is None:
                    continue
                created = datetime.fromisoformat(
                    f.created_at.replace("Z", "+00:00")
                ) if f.created_at else datetime.now(UTC)
                age_days = max(
                    0.0,
                    (datetime.now(UTC) - created).total_seconds() / 86400.0,
                )
                fact_dicts.append({
                    "fact_id": f.fact_id,
                    "position": f.langevin_position,
                    "access_count": f.access_count,
                    "age_days": age_days,
                    "importance": f.importance,
                })

            if fact_dicts:
                results = ld.batch_step(fact_dicts)
                for r in results:
                    db.update_fact(r["fact_id"], {
                        "langevin_position": r["position"],
                        "lifecycle": r["lifecycle"],
                    })
                counts["langevin_updated"] = len(results)
        except Exception as exc:
            logger.warning("Langevin maintenance failed: %s", exc)

    # 1b. Fisher-Langevin coupling: modulate temperature per-fact
    # High Fisher confidence (low variance) -> low temperature -> memory stabilizes
    # Low Fisher confidence (high variance) -> high temperature -> memory fades
    if config.math.langevin_persist_positions and counts["langevin_updated"] > 0:
        try:
            from superlocalmemory.dynamics.fisher_langevin_coupling import (
                FisherLangevinCoupling,
            )

            coupling = FisherLangevinCoupling(
                base_temperature=config.math.langevin_temperature,
            )
            coupled_count = 0

            for f in facts:
                if f.langevin_position is None or f.fisher_variance is None:
                    continue
                eff_temp = coupling.get_effective_temperature(
                    f.fisher_variance, f.access_count,
                )
                # Re-run Langevin step with Fisher-coupled temperature
                # only if it differs meaningfully from the base temperature
                if abs(eff_temp - config.math.langevin_temperature) > 0.01:
                    from superlocalmemory.math.langevin import LangevinDynamics

                    coupled_ld = LangevinDynamics(
                        dim=8,
                        dt=config.math.langevin_dt,
                        temperature=eff_temp,
                    )
                    created = datetime.fromisoformat(
                        f.created_at.replace("Z", "+00:00")
                    ) if f.created_at else datetime.now(UTC)
                    age_days = max(
                        0.0,
                        (datetime.now(UTC) - created).total_seconds() / 86400.0,
                    )
                    new_pos, weight = coupled_ld.step(
                        position=f.langevin_position,
                        access_count=f.access_count,
                        age_days=age_days,
                        importance=f.importance,
                    )
                    lifecycle = coupled_ld.get_lifecycle_state(weight).value
                    db.update_fact(f.fact_id, {
                        "langevin_position": new_pos,
                        "lifecycle": lifecycle,
                    })
                    coupled_count += 1

            counts["fisher_coupled"] = coupled_count
        except Exception as exc:
            logger.warning("Fisher-Langevin coupling failed: %s", exc)

    # 2. Sheaf batch consistency on recent facts (last 24h)
    if config.math.sheaf_at_encoding:
        try:
            from superlocalmemory.math.sheaf import SheafConsistencyChecker

            checker = SheafConsistencyChecker(
                db, config.math.sheaf_contradiction_threshold,
            )
            cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
            recent = [f for f in facts if f.created_at and f.created_at >= cutoff]
            for f in recent:
                if f.embedding and f.canonical_entities:
                    checker.check_consistency(f, profile_id)
                    counts["sheaf_checked"] += 1
        except Exception as exc:
            logger.warning("Sheaf maintenance failed: %s", exc)

    logger.info(
        "Maintenance complete: %d Langevin, %d Fisher-coupled, %d Sheaf",
        counts["langevin_updated"], counts["fisher_coupled"],
        counts["sheaf_checked"],
    )
    return counts
