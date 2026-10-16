# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""``slm outcome`` — say whether recalled memories helped.

The CLI had no way to report an outcome, so a terminal user could recall but
never teach. This sends the report to the daemon's
``/api/behavioral/report-outcome`` with ``source=cli``; the route stores it
through ``learning.outcome_events.write_outcome``, the same writer MCP uses.
So a CLI report carries the same reward, attribution and idempotence as any
other, and is settled by the same loop.

Quote the ``query_id`` that ``slm recall --json`` returned with
``--query-id``; without it the report can only be matched on overlapping
memories inside a time window.
"""

from __future__ import annotations

import sys
from argparse import Namespace
from typing import Any

__all__ = ["cmd_outcome", "register_outcome_parser", "run"]


def register_outcome_parser(sub: Any) -> None:
    """Attach ``slm outcome``. Called from cli/main.py."""
    p = sub.add_parser(
        "outcome",
        help="Report whether recalled memories helped (trains ranking)",
    )
    p.add_argument("verdict", choices=("success", "partial", "failure"))
    p.add_argument("fact_ids", nargs="+", help="Memory IDs the verdict is about")
    p.add_argument("--query-id", default="",
                   help="The query_id `slm recall --json` returned")
    p.add_argument("--note", default="", help="Optional free-text context")
    p.add_argument("--agent", default="", help="Reporting agent name")
    p.add_argument("--report-id", default="",
                   help="Idempotency key: a retry with it records nothing new")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def _body(args: Namespace) -> dict:
    return {
        "memory_ids": [str(f) for f in args.fact_ids],
        "outcome": args.verdict,
        "context": str(args.note or "")[:1000],
        "recall_query_id": str(args.query_id or "")[:64],
        "source": "cli",
        "agent_id": str(args.agent or "")[:120],
        "report_id": str(args.report_id or "")[:128],
    }


def cmd_outcome(args: Namespace) -> int:
    """Send one outcome report. Returns a process exit code."""
    from superlocalmemory.cli.commands import _daemon_unavailable
    from superlocalmemory.cli.daemon import (
        DaemonUnprocessable,
        daemon_request,
        ensure_daemon,
        is_daemon_running,
    )
    from superlocalmemory.core.admission import gate_cli_mutation
    from superlocalmemory.core.operation_request import OperationKind

    use_json = bool(getattr(args, "json", False))
    gate_cli_mutation(OperationKind.REMEMBER)
    if not (is_daemon_running() or ensure_daemon()):
        _daemon_unavailable("outcome", use_json)
        return 1
    try:
        result = daemon_request("POST", "/api/behavioral/report-outcome",
                                _body(args), preserve_unprocessable=True)
    except DaemonUnprocessable as exc:
        return _refused(str(getattr(exc, "detail", exc)), use_json)
    if not isinstance(result, dict) or not result.get("success"):
        _daemon_unavailable("outcome", use_json)
        return 1
    if use_json:
        from superlocalmemory.cli.json_output import json_print

        json_print("outcome", data=result)
    else:
        state = "already recorded" if result.get("duplicate") else "recorded"
        tie = ("tied to that recall" if result.get("attributed")
               else "matched by overlapping memories")
        print(f"Outcome {args.verdict} {state} (reward {result.get('reward')}, "
              f"{tie}): {result.get('outcome_id')}")
    return 0


def _refused(message: str, use_json: bool) -> int:
    if use_json:
        from superlocalmemory.cli.json_output import json_print

        json_print("outcome", error={"code": "INVALID", "message": message,
                                     "retryable": False})
    else:
        print(f"Refused: {message}", file=sys.stderr)
    return 1


def run(args: Namespace) -> None:
    """The ``slm outcome`` entry point: exits with the command's status."""
    rc = cmd_outcome(args)
    if rc:
        sys.exit(rc)
