# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory

"""A polite, human-only GitHub star ask for the command line.

The ask is shown to people at a terminal and nowhere else: never in --json
output, never when stdout is piped, and never in anything an agent parses
(MCP results, hook output, daemon responses). The first-recall variant is
shown once and remembered in ``ui_state.json`` in the SLM data directory.
Every failure here is swallowed on purpose: a star prompt must never break
a command, and when state cannot be read or written it is simply not shown.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_URL = "https://github.com/qualixar/superlocalmemory"
ASK_LINE = "If SuperLocalMemory helps you, a GitHub star helps other developers find it:"
HELP_FOOTER = f"Like SLM? Star it: {REPO_URL}"
STATE_FILE = "ui_state.json"
STATE_KEY = "star_prompt_shown"


def is_human_terminal(args=None) -> bool:
    """True only for an interactive terminal with no machine-output flag."""
    if args is not None and (getattr(args, "json", False) or getattr(args, "quiet", False)):
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - a closed or odd stdout is "not a terminal"
        return False


def ask_lines(indent: str = "  ") -> list[str]:
    return [f"{indent}{ASK_LINE}", f"{indent}  {REPO_URL}"]


def print_setup_ask() -> None:
    """Print the ask at the end of ``slm setup`` (terminal only)."""
    if not is_human_terminal():
        return
    print()
    print("\n".join(ask_lines()))


def print_help_footer() -> None:
    """Print the one-line footer of ``slm help`` (terminal only)."""
    if is_human_terminal():
        print(HELP_FOOTER)


def _state_path() -> Path:
    from superlocalmemory.infra.data_root import canonical_data_root

    return canonical_data_root() / STATE_FILE


def _already_shown(path: Path) -> bool:
    """Fail closed: unreadable state counts as shown, so we never nag."""
    try:
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(isinstance(data, dict) and data.get(STATE_KEY))
    except Exception:  # noqa: BLE001
        return True


def _mark_shown(path: Path) -> bool:
    try:
        data: dict = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data = dict(loaded) if isinstance(loaded, dict) else {}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({**data, STATE_KEY: True}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001
        return False


def maybe_print_first_recall_ask(args, result_count: int) -> bool:
    """Show the ask once, after the first recall that returned results.

    Returns True when the ask was printed. The flag is written first, and the
    ask is shown only if the write succeeded, so a read-only data directory
    can never turn a one-time ask into a repeating one.
    """
    try:
        if result_count < 1 or not is_human_terminal(args):
            return False
        path = _state_path()
        if _already_shown(path) or not _mark_shown(path):
            return False
        print()
        print("\n".join(ask_lines()))
        return True
    except Exception:  # noqa: BLE001
        return False
