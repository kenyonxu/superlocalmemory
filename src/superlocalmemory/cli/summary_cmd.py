# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com

"""``slm summary`` — the readable layer over your memories (issue #113).

WHY THIS FILE EXISTS
--------------------
4.0.6 shipped the three generators in ``superlocalmemory/summaries/`` with no way
to call them: no command, no MCP tool, no route. The changelog listed the feature
as added, the issue reply said it had landed, and a user could do nothing with it.
This is that missing surface.

Three summaries, each bounded and traceable:

  ``slm summary session <id>``   what one session covered
  ``slm summary day [DATE]``     what a day's main topics were
  ``slm summary project <path>`` what was worked on in a project

Every result states its coverage. Session data in particular is sparse — roughly
4% of facts carry a session id on a real store — so a session summary reports what
fraction it could actually see rather than presenting a slice as the whole.

No language model is required: the generators are extractive by default, so this
works in Local Guardian mode with nothing installed.
"""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from superlocalmemory.infra.data_root import state_path

def _coverage_is_complete(coverage: str) -> bool:
    """Whether *coverage* means "this really is the whole picture".

    Deliberately inverted. My first version listed the values that needed a
    caveat — ``("partial", "sparse", "none", "empty")`` — and three of those four
    are not values this system emits. The real vocabulary is COVERAGE_FULL /
    PARTIAL / INSUFFICIENT / NO_SESSION / UNAVAILABLE, so a session summary
    reporting "no_session" printed no caveat at all: the one honesty feature
    issue #113 asked for, silently inactive.

    Testing for completeness instead means any value that is not FULL — including
    one added later — gets the caveat. The failure mode becomes an unnecessary
    warning rather than a missing one.
    """
    try:
        from superlocalmemory.summaries.base import COVERAGE_FULL

        return coverage == COVERAGE_FULL
    except Exception:
        return coverage == "full"


def _db_path() -> Path:
    return state_path("memory.db")


def _active_profile() -> str:
    """Resolve the active profile without a daemon and without side effects.

    Reads the ``active`` pointer out of ``profiles.json`` directly.
    ``ProfileManager`` would give the same answer, but its constructor calls
    ``mkdir(parents=True)`` — creating directories is not something a read-only
    summary command should do. ``core.profiles`` also has no module-level
    accessor; ``get_active_profile`` there is a method on the manager, and the
    module-level one lives in ``server/routes/helpers.py``, which the CLI must
    not import.
    """
    try:
        from superlocalmemory.core.profiles import DEFAULT_PROFILES_FILE

        path = state_path(DEFAULT_PROFILES_FILE)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            active = raw.get("active")
            if isinstance(active, str) and active:
                return active
    except Exception:
        pass
    return "default"


def _emit(result: Any, as_json: bool) -> None:
    """Print a SummaryResult as JSON or as prose."""
    if as_json:
        print(json.dumps({
            "kind": result.kind,
            "profile_id": result.profile_id,
            "content": result.content,
            "source_fact_ids": result.source_fact_ids,
            "coverage": result.coverage,
            "generated_by": result.generated_by,
            "metadata": result.metadata,
        }, indent=2, default=str))
        return

    print()
    print(result.content.rstrip() or "(nothing recorded)")
    print()

    # Coverage is not decoration. A summary built from a fraction of the data
    # that presents itself as the whole is the failure mode issue #113 called
    # out by name, so it is stated on every single result, not only bad ones.
    n = len(result.source_fact_ids)
    line = f"Built from {n} memor{'y' if n == 1 else 'ies'} · coverage: {result.coverage}"
    if not _coverage_is_complete(result.coverage):
        line += " — treat as a partial view, not a complete record"
    print(line)
    if result.generated_by:
        print(f"Method: {result.generated_by}")
    print("Use --json to see the exact memories this came from.")


def cmd_summary(args: Namespace) -> None:
    """Dispatch ``slm summary <subcommand>``."""
    sub = getattr(args, "summary_command", None)
    as_json = bool(getattr(args, "json", False))
    profile = getattr(args, "profile", None) or _active_profile()
    db = _db_path()

    if not db.exists():
        print(f"No memory database at {db}. Run `slm status` first.")
        return

    if sub == "session":
        from superlocalmemory.summaries import generate_session_summary

        _emit(generate_session_summary(db, args.session_id, profile), as_json)
        return

    if sub == "day":
        from superlocalmemory.summaries import generate_daily_reflection

        target = getattr(args, "date", None) or date.today().isoformat()
        if target == "yesterday":
            target = (date.today() - timedelta(days=1)).isoformat()
        elif target == "today":
            target = date.today().isoformat()
        _emit(generate_daily_reflection(db, target, profile), as_json)
        return

    if sub == "project":
        from superlocalmemory.summaries import generate_project_work_log

        path = getattr(args, "path", None) or str(Path.cwd())
        _emit(generate_project_work_log(db, path, profile), as_json)
        return

    print("Usage: slm summary {session <id> | day [DATE] | project [PATH]}")
    print()
    print("  slm summary day                 what you recorded today")
    print("  slm summary day yesterday       ...or yesterday")
    print("  slm summary day 2026-08-17      ...or a specific date")
    print("  slm summary project             work log for the current directory")
    print("  slm summary session <id>        what one session covered")
    print()
    print("Add --json to include the ids of the memories a summary came from.")


def register_summary_parser(sub: Any) -> None:
    """Attach the ``summary`` parser. Called from cli/main.py."""
    p = sub.add_parser(
        "summary",
        help="Readable summaries of your memories (session, day, project)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--profile", help="profile to summarise (default: active)")
    ssub = p.add_subparsers(dest="summary_command", title="summary subcommands")

    s = ssub.add_parser("session", help="what one session covered")
    s.add_argument("session_id", help="session id (see `slm status`)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--profile")

    d = ssub.add_parser("day", help="what a day's main topics were")
    d.add_argument(
        "date", nargs="?",
        help="YYYY-MM-DD, 'today' or 'yesterday' (default: today)",
    )
    d.add_argument("--json", action="store_true")
    d.add_argument("--profile")

    pr = ssub.add_parser("project", help="what was worked on in a project")
    pr.add_argument(
        "path", nargs="?",
        help="project directory (default: current directory)",
    )
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--profile")
