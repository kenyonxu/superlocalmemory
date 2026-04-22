# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""MemoryEngine capability levels.

LIGHT: SQLite + profile only. Suitable for clients that must stay small in
memory (e.g. multi-IDE MCP processes) and route heavy work elsewhere.

FULL: DB layer + embedder + retrieval engine + LLM. Matches the historical
v3.4.25 engine surface exactly and remains the default for backward compat.
"""
from __future__ import annotations

import enum


class Capabilities(enum.Enum):
    LIGHT = "light"
    FULL = "full"


class CapabilityError(RuntimeError):
    """Raised when a LIGHT-mode engine is asked for a FULL-mode operation."""
