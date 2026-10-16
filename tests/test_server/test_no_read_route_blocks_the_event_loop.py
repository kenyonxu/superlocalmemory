# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A read route is never ``async def`` unless it awaits something.

An ``async def`` route with no ``await`` runs on the event loop; whatever it
reads or waits for stops the daemon answering anything else. A plain ``def``
route runs on a worker thread.
"""

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "src" / "superlocalmemory" / "server"
STAYS_ON_LOOP = {"/api/version"}
LOOP_NAMES = {"asyncio", "anyio", "get_running_loop", "get_event_loop", "create_task",
              "ensure_future", "call_soon"}


def _offenders():
    found = []
    for path in sorted([*(SERVER / "routes").glob("*.py"), SERVER / "unified_daemon.py"]):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            gets = [d for d in node.decorator_list
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "get"]
            if not gets:
                continue
            if any(isinstance(x, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for x in ast.walk(node)):
                continue
            names = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)} | \
                    {x.attr for x in ast.walk(node) if isinstance(x, ast.Attribute)}
            route = gets[0].args[0].value if gets[0].args and isinstance(gets[0].args[0], ast.Constant) else "?"
            if route in STAYS_ON_LOOP or names & LOOP_NAMES:
                continue
            found.append(f"{path.name}:{node.lineno} {route}")
    return found


def test_no_read_route_is_async_without_awaiting():
    assert _offenders() == []
