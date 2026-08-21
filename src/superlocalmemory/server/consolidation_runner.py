# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com

"""One implementation of full consolidation, shared by every trigger.

WHY THIS EXISTS
---------------
Steps 8-11 of ``ConsolidationEngine.consolidate()`` — behavioural assertion
mining, soft prompts, skill performance, skill evolution — only run on the
``lightweight=False`` path. Before 4.0.8 **nothing automatic ever took that
path**:

* ``consolidation_engine.py`` self-triggers with ``lightweight=True`` only.
* The session-end hook shelled out to ``slm consolidate --cognitive``, which
  runs ``CognitiveConsolidator.run_pipeline()`` — a different class that does
  not contain steps 8-11 at all.
* ``POST /consolidation/trigger`` did run the full path, but only when a human
  called it.

The measurable consequence on a real store: ``behavioral_assertions`` sat at 0
rows while the miner, run once by hand against the same data, produced 9
assertions immediately. The Behaviour tab was empty because the miner had never
executed, not because there was nothing to mine.

DESIGN
------
Two triggers, one implementation, one lock:

* the daemon's periodic timer (the correctness guarantee), and
* the session-end hook posting to ``/consolidation/trigger`` (the fast path).

``_LOCK`` serialises them. A second trigger arriving while one is running is
**skipped, not queued** — consolidation is idempotent catch-up work, so running
it twice back to back buys nothing and doubles the write pressure.

HOT PATH
--------
Never called from store or recall. The engine call is CPU/IO bound for seconds
to minutes, so it runs inside ``asyncio.to_thread`` — blocking a worker thread
is fine, blocking the event loop is not. The periodic trigger additionally
refuses to start unless the daemon has been idle (see ``unified_daemon``), so
scheduled consolidation cannot land in the middle of a burst of remember/recall
traffic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("superlocalmemory.consolidation_runner")

#: Serialises the timer and the hook. Module-level: there is one daemon.
_LOCK = asyncio.Lock()


def is_running() -> bool:
    """True when a consolidation pass currently holds the lock."""
    return _LOCK.locked()


def _consolidate_blocking(app_state: Any, profile_id: str, lightweight: bool) -> dict:
    """Run the engine synchronously. Caller must put this in a thread.

    Lifted verbatim from ``POST /consolidation/trigger`` so the endpoint and the
    timer cannot drift apart — two copies of this would be two different
    definitions of "consolidated".
    """
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.consolidation_engine import ConsolidationEngine
    from superlocalmemory.server.profile_runtime import get_profile_runtime
    from superlocalmemory.storage import schema as _schema
    from superlocalmemory.storage.database import DatabaseManager

    runtime = get_profile_runtime(app_state)
    # Rule 18: hold the operation lease so a concurrent profile switch cannot
    # commit halfway through a consolidation.
    with runtime.operation():
        config = SLMConfig.load()
        db = DatabaseManager(config.db_path)
        db.initialize(_schema)
        engine = ConsolidationEngine(
            db=db, config=config.consolidation, slm_config=config,
        )
        res = engine.consolidate(profile_id=profile_id, lightweight=lightweight)

        # Behavioural pattern mining writes learning.db rather than memory.db, so
        # it sits outside the engine. Kept non-fatal: a pattern-mining failure
        # must not discard a completed consolidation.
        try:
            from superlocalmemory.learning.consolidation_worker import (
                ConsolidationWorker,
            )
            learning_db = config.base_dir / "learning.db"
            cw = ConsolidationWorker(str(config.db_path), str(learning_db))
            res["patterns_mined"] = cw._generate_patterns(profile_id, False)
        except Exception as exc:
            logger.warning("pattern mining after consolidation failed: %s", exc)
            # -1, not 0: "it broke" and "there was nothing to mine" are
            # different answers and the dashboard must not conflate them.
            res["patterns_mined"] = -1
    return res


async def run_full_consolidation(
    app_state: Any,
    profile_id: str,
    *,
    lightweight: bool = False,
    trigger: str = "manual",
) -> dict:
    """Run one consolidation pass, or skip if one is already in flight.

    Returns the engine result dict, plus ``trigger``. When a pass is already
    running the result is ``{"skipped": True, "reason": "already running"}`` —
    an explicit skip rather than a silent no-op, so a caller can tell "did not
    need to run" from "ran and found nothing".
    """
    if _LOCK.locked():
        logger.info("consolidation skipped (%s): a pass is already running", trigger)
        return {"skipped": True, "reason": "already running", "trigger": trigger}

    async with _LOCK:
        logger.info(
            "consolidation starting (trigger=%s, profile=%s, lightweight=%s)",
            trigger, profile_id, lightweight,
        )
        result = await asyncio.to_thread(
            _consolidate_blocking, app_state, profile_id, lightweight,
        )
        logger.info(
            "consolidation finished (trigger=%s): assertions=%s patterns=%s",
            trigger,
            (result.get("assertions") or {}).get("created", "n/a"),
            result.get("patterns_mined", "n/a"),
        )
        result["trigger"] = trigger
        return result
