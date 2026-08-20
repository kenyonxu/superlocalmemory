# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Shared I/O helpers and compliance constants for ``slm gdpr`` subcommands.

Separated from gdpr_cmd.py to honour the 800-line file cap (coding-style.md).
All symbols here are internal (prefixed ``_``) or ALL_CAPS constants.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def _die(message: str, code: int = 1) -> None:
    """Print an actionable error to stderr and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def _print_json(data: Any) -> None:
    """Emit pretty-printed JSON to stdout (evidence-pipeline format)."""
    print(_json.dumps(data, indent=2, default=str))


def _json_envelope(
    command: str, *, data: dict | None = None, error: dict | None = None
) -> dict:
    """Build the standard agent-native JSON envelope used across all SLM CLIs."""
    from superlocalmemory.cli.json_output import _get_version

    env: dict = {
        "success": error is None,
        "command": command,
        "version": _get_version(),
    }
    if error is not None:
        env["error"] = error
    else:
        env["data"] = data if data is not None else {}
    return env


# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

def _data_root() -> Path:
    """Resolve the canonical SLM data root (honours SLM_DATA_DIR env var)."""
    from superlocalmemory.infra.data_root import canonical_data_root

    return canonical_data_root()


def _db_path() -> Path:
    return _data_root() / "memory.db"


def _audit_chain_path() -> Path:
    return _data_root() / "audit_chain.db"


# ─────────────────────────────────────────────────────────────────────────────
# GDPR compliance constants (DPO-facing, surfaced in ``status`` output)
# ─────────────────────────────────────────────────────────────────────────────

# Known gaps in this release — reported honestly so a DPO can plan remediation.
# Gaps C1 and C2 (cross-device erase verification and audit-log replay) remain open.
KNOWN_GAPS = [
    {
        "ref": "C1",
        "summary": "backups/ directory is outside erasure scope",
        "detail": (
            "Up to 10 rotating snapshots of memory.db, learning.db and "
            "related DB files survive an Art.17 erasure.  The erasure receipt "
            "records a 'backup_obligation' so the obligation is tracked, but "
            "the snapshots are not automatically purged.  Snapshots are "
            "re-erased on restore."
        ),
    },
    {
        "ref": "C2",
        "summary": "code_graph.db is not in erasure scope",
        "detail": (
            "Repository paths, file names and symbol names (identifying data "
            "in a work context) are stored in code_graph.db.  This file is "
            "covered by compliance/gdpr.py but the per-table discovery "
            "operates on the main memory.db only."
        ),
    },
]

# Art. coverage flags that compliance/gdpr.py actively implements.
ART_COVERAGE = {
    "art15_right_to_access": True,
    "art17_right_to_erasure": True,
    "art20_right_to_portability": True,
    "backup_scope": "outstanding_obligation_recorded_on_receipt",
    "audit_chain": "HMAC-chained tamper-evident",
    "erasure_fails_closed": True,
}
