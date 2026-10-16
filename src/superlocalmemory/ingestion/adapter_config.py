"""Supported adapter tiers and startup requirements shared by setup and UI."""

from __future__ import annotations

import os
from pathlib import Path

TIERS = {
    "gmail": {"auto", "mbox", "imap", "oauth"},
    "calendar": {"auto", "ics", "oauth"},
    "transcript": {"auto"},
}


def validate_tier(name: str, tier: str) -> str:
    if not isinstance(tier, str) or tier not in TIERS.get(name, set()):
        raise ValueError(
            f"Unsupported {name} tier. Choose one of: {', '.join(sorted(TIERS.get(name, [])))}"
        )
    return tier


def normalize_config(config: dict) -> tuple[dict, bool]:
    result = {name: dict(value) for name, value in config.items()}
    changed = False
    for name, value in result.items():
        tier = value.get("tier", "auto")
        if name in TIERS and (not isinstance(tier, str) or tier not in TIERS[name]):
            value["tier"] = "auto"
            changed = True
    return result, changed


def setup_error(name: str, config: dict) -> str:
    try:
        validate_tier(name, config.get("tier", "auto"))
    except ValueError as exc:
        return str(exc)
    if name == "transcript":
        path = config.get("watch_dir", "")
        if not path or not Path(path).is_dir() or not os.access(path, os.R_OK | os.X_OK):
            return "Choose a readable transcript folder in Configure, then retry Start."
    return ""
