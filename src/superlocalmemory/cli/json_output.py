# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Shared JSON envelope for agent-native CLI output.

Follows the 2026 agent-native CLI standard:
- Consistent envelope: success, command, version, data/error
- HATEOAS next_actions for agent guidance
- Metadata for execution context

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json


def _get_version() -> str:
    """Get installed package version."""
    try:
        from importlib.metadata import version
        return version("superlocalmemory")
    except Exception:
        return "unknown"


def json_print(
    command: str,
    *,
    data: dict | None = None,
    error: dict | None = None,
    next_actions: list[dict] | None = None,
    metadata: dict | None = None,
) -> None:
    """Print a standard JSON envelope to stdout.

    Success envelope:
        {"success": true, "command": "...", "version": "...", "data": {...}}

    Error envelope:
        {"success": false, "command": "...", "version": "...", "error": {...}}
    """
    envelope: dict = {
        "success": error is None,
        "command": command,
        "version": _get_version(),
    }
    if error is not None:
        envelope["error"] = error
    else:
        envelope["data"] = data if data is not None else {}
    if metadata:
        envelope["metadata"] = metadata
    if next_actions:
        envelope["next_actions"] = next_actions
    print(json.dumps(envelope, indent=2, default=str))
