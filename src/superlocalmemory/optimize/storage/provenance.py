# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Which profile a cached or compressed text belongs to, recorded at write time.

The Optimize stores are keyed by agent, not by person, so the only link between
a cached text and a profile is the profile that was active when it was written.
Without it an erasure cannot find the rows (see ``optimize.storage.erasure``).
"""

from __future__ import annotations

import json


def active_profile_id() -> str:
    """The profile this request works on, or ``''`` when it cannot be told.

    Never raises: provenance is recorded best-effort, and ``''`` makes the row
    one that every later erasure treats as possibly its own.
    """
    try:
        from superlocalmemory.server.profile_runtime import current_request_profile

        named = current_request_profile()
        if named:
            return str(named)
    except Exception:  # noqa: BLE001
        pass
    try:
        from superlocalmemory.infra.data_root import state_path

        data = json.loads(state_path("profiles.json").read_text(encoding="utf-8"))
        return str(data.get("active_profile") or data.get("active") or "")
    except Exception:  # noqa: BLE001
        return ""
