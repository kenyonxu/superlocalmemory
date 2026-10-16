# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Measured raw-cosine floors, keyed by exact embedding-space identity.

Both EG2 and Nomic use the same store job and holdout procedure. Unmeasured
spaces retain the configured floor; retrieval-v1 has a synthetic bootstrap.
The config's user value is never overwritten by a transient calibration.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from superlocalmemory.retrieval.judge_calibration_file import FILE_NAME, SCHEMA, parse_entries

# scripts/quality/wp6_measure.py: synthetic training optimum 0.5494, rounded
# down for answer recall. Never presented as measured on the user's store.
NOMIC_BOOTSTRAP_FLOOR = 0.54


def floor_for(root: Path, signature: str) -> float | None:
    try:
        payload = json.loads((root / FILE_NAME).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            return None
        parse_entries(payload)
        semantic = payload.get("semantic", [])
        if not isinstance(semantic, list) or any(not isinstance(e, dict) for e in semantic):
            return None
        found = [entry for entry in semantic if entry.get("signature") == signature]
        if len(found) != 1:
            return None
        entry = found[0]
        floor = entry["raw_floor"]
        if isinstance(floor, bool) or not isinstance(floor, (int, float)):
            return None
        if not math.isfinite(floor) or not 0 <= floor <= 1 or entry.get("usable", True) is not True:
            return None
        counts = (entry.get("answered", 0), entry.get("unanswered", 0))
        if any(type(value) is not int for value in counts) or counts[0] < 28 or counts[1] < 14:
            return None
        return float(floor)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def apply_to(config, embedder=None) -> None:
    """Bind a provider to its real space/root; presets and fresh providers share this."""
    if embedder is None or not hasattr(config, "base_dir"):
        return  # a provider-only staging config has no retrieval policy
    from superlocalmemory.storage.embedding_spaces import signature_of
    try:
        embedder._calibration_root = config.base_dir
        embedder._calibration_signature = signature_of(config.embedding)
    except (AttributeError, TypeError):
        pass  # immutable external stand-ins still derive identity below
