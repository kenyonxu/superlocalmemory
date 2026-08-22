# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Move an existing store onto the graph and vector backends, by itself.

Cozo (graph) and LanceDB (vectors) have shipped as required dependencies since
3.7, and until now they sat unused: the projections were only built when
somebody ran `slm db scale prepare` and then `verify` and then `promote` by
hand. Almost nobody did, so almost every store kept answering graph and
similarity questions out of SQLite.

This runs that same sequence on the first start after an upgrade, so an existing
user gets the backends without doing anything.

WHAT IT WILL NOT DO

**It will not leave a store worse than it found it.** The lifecycle it drives
already stages, verifies against the canonical counts, and keeps a rollback —
this only decides when to call it. If verification does not match, nothing is
promoted and the store keeps answering from SQLite.

**It will not stop a store from working.** If the libraries are absent or fail
to import, or the projection cannot be built, the daemon serves from SQLite and
says so. Refusing to start would be a worse outcome than the one being fixed:
a user whose native extension does not match their interpreter would have no
product at all, and that is not a hypothetical — it was the state of the
author's own machine while this was written.

**It will not run twice.** Once the state is `promoted` there is nothing to do,
and a store mid-repair is left for the repair path rather than restarted.

WHAT IT COSTS, MEASURED

On a real 604 MB store — 5,283 memories, 137,104 edges, 395,107 links, 5,278
vectors — prepare took 10 s, verify 5 s, promote 5 s, and the two projections
occupy 16 MB each. It happens once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AutoPromotionResult", "auto_promote_scale_backends"]

#: The two the projections need. Absence is a reason to stay on SQLite, never a
#: reason to fail.
REQUIRED_LIBRARIES = ("pycozo", "lancedb")


@dataclass(frozen=True)
class AutoPromotionResult:
    """What happened, in terms a status endpoint can show a person."""

    attempted: bool
    promoted: bool
    reason: str
    stage_id: str = ""
    restart_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "promoted": self.promoted,
            "reason": self.reason,
            "stage_id": self.stage_id,
            "restart_required": self.restart_required,
        }


def _missing_libraries() -> list[str]:
    import importlib

    missing: list[str] = []
    for name in REQUIRED_LIBRARIES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - any import failure means unusable
            missing.append(name)
    return missing


def auto_promote_scale_backends(config: Any) -> AutoPromotionResult:
    """Build and promote the projections if this store has not got them yet.

    Returns what happened rather than raising: every outcome here is a state the
    daemon carries on from, and the caller shows it.
    """
    state = str(getattr(config, "scale_engine_state", "") or "local_core").lower()
    if state == "promoted":
        return AutoPromotionResult(False, True, "already promoted")

    missing = _missing_libraries()
    if missing:
        logger.warning(
            "graph and vector backends unavailable (%s will not import); "
            "serving from SQLite", ", ".join(missing),
        )
        return AutoPromotionResult(
            False, False, f"{', '.join(missing)} will not import",
        )

    try:
        from superlocalmemory.core.scale_engine import ScaleEngineManager
    except Exception as exc:  # noqa: BLE001
        return AutoPromotionResult(False, False, f"scale engine unavailable: {exc}")

    try:
        manager = ScaleEngineManager(config)
    except Exception as exc:  # noqa: BLE001
        return AutoPromotionResult(False, False, f"could not open the store: {exc}")

    try:
        status = manager.status()
    except Exception as exc:  # noqa: BLE001
        return AutoPromotionResult(False, False, f"could not read the state: {exc}")

    if status.get("migration_repair_required"):
        # An interrupted promotion has its own recovery path, and starting a
        # fresh one on top of it would be building over a half-finished move.
        logger.warning(
            "a previous promotion did not finish; leaving it for repair rather "
            "than starting another",
        )
        return AutoPromotionResult(False, False, "a previous promotion needs repair")

    try:
        prepared = manager.prepare()
        stage_id = str(prepared.get("stage_id", ""))
        verified = manager.verify(stage_id)
        if str(verified.get("state")) != "verified":
            logger.warning(
                "projection did not match the canonical store; not promoting",
            )
            return AutoPromotionResult(
                True, False, "the projection did not match the store", stage_id,
            )
        promoted = manager.promote(stage_id)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logger.warning(
            "could not move onto the graph and vector backends (%s); serving "
            "from SQLite", exc,
        )
        return AutoPromotionResult(True, False, str(exc))

    logger.info(
        "graph and vector backends promoted (stage %s); they serve after the "
        "next start", promoted.get("stage_id", ""),
    )
    return AutoPromotionResult(
        True,
        True,
        "promoted",
        str(promoted.get("stage_id", "")),
        bool(promoted.get("restart_required", True)),
    )
