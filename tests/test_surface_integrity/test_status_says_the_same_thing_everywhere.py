# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Three surfaces answer "what is the state of this store". They should agree.

They did not. The HTTP surface — the one a person actually looks at — omitted
the entity and edge counts, the store's own path and size, and the profile
generation; the other two omitted the version. Its fact count came from SQL that
skipped the visibility predicate, so it counted soft-deleted and withheld rows
and read high. And the CLI upper-cased the mode in its JSON, so a client
comparing the two answers found one field that never matched.

This runs all three against one store and compares. No daemon: each surface has
a no-daemon path, and a parity test that skips when infrastructure is missing is
a parity test that never runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from superlocalmemory.core.status_contract import CANONICAL_STATUS_FIELDS

# The mode is reported by every surface; a store's counts must be identical
# because all three are reading the same rows at the same moment.
MUST_MATCH = (
    "mode", "provider", "profile", "base_dir", "db_path",
    "fact_count", "entity_count", "edge_count", "profile_generation", "version",
)


class _ToolCollector:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(func):
            self.tools[func.__name__] = func
            return func
        return decorator


def _seed_fact(engine, profile_id: str, fid: str, *, archived=False, withheld=False) -> None:
    engine._db.execute(
        "INSERT INTO memories "
        "(memory_id, profile_id, content, session_id, speaker, role, "
        " created_at, metadata_json, scope) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"mem-{fid}", profile_id, "seed memory", "sess-1", "user", "user",
         "2026-01-01T00:00:00Z", "{}", "personal"),
    )
    engine._db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, lifecycle) "
        "VALUES (?, ?, ?, ?, ?)",
        (fid, f"mem-{fid}", profile_id, f"content {fid}", "active"),
    )
    if archived:
        engine._db.execute(
            "UPDATE atomic_facts SET archive_status = 'archived' WHERE fact_id = ?",
            (fid,),
        )
    if withheld:
        engine._db.execute(
            "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?", (fid,),
        )


@pytest.fixture
def store(engine_with_mock_deps):
    """One engine, one store on disk, three surfaces pointed at it.

    Seeded with rows of both excluded kinds. Without them the count assertions
    below compare zero against zero and pass whatever the predicate does.
    """
    engine = engine_with_mock_deps
    engine.profile_id = "default"
    engine._config.active_profile = "default"
    engine._db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
        ("default", "default"),
    )
    for i in range(4):
        _seed_fact(engine, "default", f"visible-{i}")
    _seed_fact(engine, "default", "gone-1", archived=True)
    _seed_fact(engine, "default", "gone-2", archived=True)
    _seed_fact(engine, "default", "held-1", withheld=True)

    visible = engine._db.get_fact_count("default")
    total = engine._db.execute(
        "SELECT COUNT(*) AS c FROM atomic_facts WHERE profile_id = ?", ("default",),
    )
    assert visible == 4, f"fixture seeded wrong: {visible} visible"
    assert int(dict(total[0])["c"]) == 7, "fixture seeded wrong: total"
    return engine


def _http_status(engine) -> dict:
    from superlocalmemory.server.profile_runtime import bind_profile_runtime
    from superlocalmemory.server.unified_daemon import create_app

    app = create_app()
    app.state.engine = engine
    app.state.config = engine._config
    bind_profile_runtime(app.state, engine, engine._config)
    response = TestClient(app).get("/api/v3/dashboard")
    assert response.status_code == 200, response.text
    return response.json()


def _mcp_status(engine) -> dict:
    from superlocalmemory.mcp import tools_core

    collector = _ToolCollector()
    tools_core.register_core_tools(collector, lambda: engine)
    result = asyncio.run(collector.tools["get_status"]())
    assert result.get("success") is True, result
    return result


def _cli_status(engine) -> dict:
    env = {**os.environ, "SLM_DATA_DIR": str(engine._config.base_dir)}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_SRC), env.get("PYTHONPATH", "")],
    ).rstrip(os.pathsep)
    out = subprocess.run(
        [sys.executable, "-m", "superlocalmemory.cli.main", "status", "--json"],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert out.returncode == 0, f"slm status failed: {out.stderr[-600:]}"
    # A first run may print an upgrade banner before the document.
    return json.loads(out.stdout[out.stdout.index("{"):])["data"]


from pathlib import Path  # noqa: E402 — used by _cli_status above

_SRC = Path(__file__).resolve().parents[2] / "src"


def test_every_surface_emits_every_agreed_field(store) -> None:
    surfaces = {
        "HTTP /api/v3/dashboard": _http_status(store),
        "MCP get_status": _mcp_status(store),
        "CLI slm status --json": _cli_status(store),
    }
    for name, payload in surfaces.items():
        missing = [f for f in CANONICAL_STATUS_FIELDS if f not in payload]
        assert not missing, f"{name} is missing {missing}"


def test_the_surfaces_agree_on_what_they_report(store) -> None:
    http = _http_status(store)
    mcp = _mcp_status(store)
    cli = _cli_status(store)

    disagreements = []
    for field in MUST_MATCH:
        values = {"HTTP": http.get(field), "MCP": mcp.get(field), "CLI": cli.get(field)}
        if len(set(map(str, values.values()))) > 1:
            disagreements.append(f"{field}: {values}")

    assert not disagreements, "surfaces disagree —\n  " + "\n  ".join(disagreements)


def test_the_fact_count_is_the_one_an_owner_would_recognise(store) -> None:
    """Every surface must exclude rows no caller may be shown.

    The HTTP surface counted ``atomic_facts`` raw. On the author's store that
    reported 5,317 where the other two reported 4,018 — 1,299 soft-deleted and
    withheld rows presented as memories the owner has.
    """
    visible = store._db.get_fact_count("default")
    for name, payload in (
        ("HTTP", _http_status(store)),
        ("MCP", _mcp_status(store)),
        ("CLI", _cli_status(store)),
    ):
        assert payload["fact_count"] == visible, (
            f"{name} reports {payload['fact_count']} facts; "
            f"{visible} are visible to a caller"
        )
