# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Install readiness and execution readiness are separate observations."""

from __future__ import annotations

from superlocalmemory.core.answer_check_reload import NOTICE, changed_on_disk


def execution_status(judge, engine):
    if engine is not None and changed_on_disk(engine, judge=judge):
        return {"state": "unavailable", "detail": NOTICE}
    if judge is None:
        return {"state": "off", "detail": "Answer check is off"}
    permanent = getattr(judge, "_permanent_error", "")
    if isinstance(permanent, str) and permanent:
        return {
            "state": "unavailable",
            "detail": "On-device check failed permanently. Check or repair this install.",
        }
    if getattr(judge, "loading", False) is True:
        return {"state": "warming", "detail": "The on-device model is warming"}
    if getattr(judge, "ready", False) is True:
        return {"state": "ready", "detail": "Answer check is ready"}
    return {"state": "unavailable", "detail": "The answer check is not ready to serve"}
