# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""``tool_events`` gets written by the hook that watches tools finish.

Three separate readers -- assertion mining, skill-performance mining, and the
engagement features that settle a recall against what the agent did next -- all
select from ``tool_events``. None of them writes it. The writers were an
explicit ``log_tool_event`` call and a bulk importer, neither of which runs on
an ordinary install, so the table stopped receiving invocations and every reader
downstream of it quietly went blind.

The hook already fires on the right edge with the right session, and until now
it returned at the first line of its marker check -- the branch that is taken
for the great majority of tool calls, since nothing obliges an agent to repeat a
marker back. So the action itself, which is what those readers want, was thrown
away precisely when there was no marker to distract from it.

These tests hold the separation: recording that a tool ran and deciding what a
recall was worth are different questions, and the first does not wait on the
second.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.hooks import post_tool_outcome_hook as h

_SCHEMA = (
    "CREATE TABLE tool_events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " session_id TEXT NOT NULL,"
    " profile_id TEXT DEFAULT 'default',"
    " project_path TEXT DEFAULT '',"
    " tool_name TEXT NOT NULL,"
    " event_type TEXT NOT NULL DEFAULT 'invoke',"
    " input_summary TEXT DEFAULT '',"
    " output_summary TEXT DEFAULT '',"
    " duration_ms INTEGER DEFAULT 0,"
    " metadata TEXT DEFAULT '{}',"
    " created_at TEXT NOT NULL)"
)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A memory.db with just the table this hook writes, wired to the hook."""
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute(_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(h, "_memory_db_path", lambda: db)
    return db


@pytest.fixture()
def feed(monkeypatch):
    """Feed a payload to the hook in place of Claude Code's stdin JSON.

    Scoped to the test: ``read_stdin_json`` is imported into the hook's own
    namespace, so patching it there without restoring would leak into every
    test that ran afterwards.
    """
    def _feed(payload: dict) -> None:
        monkeypatch.setattr(h, "read_stdin_json", lambda: payload)
    return _feed


def _rows(db):
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM tool_events").fetchall()
    finally:
        conn.close()


def test_an_action_is_recorded_even_when_no_memory_was_named(store, feed) -> None:
    """The regression: the no-marker branch is the common one, not a dead end."""
    payload = {
        "session_id": "sess-1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "tool_response": "total 0\ndrwxr-xr-x  2 user staff  64 Jan  1 00:00 .",
        "cwd": "/tmp/project",
    }
    feed(payload)

    outcome = h._inner_main()  # noqa: SLF001

    assert outcome == "no_marker", "precondition: this payload names no memory"
    rows = _rows(store)
    assert len(rows) == 1, "the action still happened and must be recorded"
    assert rows[0]["tool_name"] == "Bash"
    assert rows[0]["session_id"] == "sess-1"
    assert rows[0]["event_type"] == "complete"
    assert rows[0]["project_path"] == "/tmp/project"
    assert "ls -la" in rows[0]["input_summary"]


def test_a_tool_that_returned_nothing_is_still_an_action(store, feed) -> None:
    """An empty response ends the marker path early; the invocation is real."""
    feed({
        "session_id": "sess-2",
        "tool_name": "Read",
        "tool_response": "",
    })

    outcome = h._inner_main()  # noqa: SLF001

    assert outcome == "no_response"
    assert len(_rows(store)) == 1


def test_an_unnamed_tool_is_not_recorded(store, feed) -> None:
    """Without a tool name there is no action to attribute; write nothing."""
    feed({"session_id": "sess-3", "tool_name": "", "tool_response": "x"})

    h._inner_main()  # noqa: SLF001

    assert _rows(store) == []


def test_credentials_do_not_reach_the_stored_summary(store) -> None:
    """Telemetry is durable, so a secret written into it stays written."""
    h._record_tool_event(  # noqa: SLF001
        "sess-4",
        "Bash",
        {"tool_input": {"command": "export API_KEY=sk-abcdefghijklmnop1234"}},
        "password = hunter2seventeen",
    )

    row = _rows(store)[0]
    assert "sk-abcdefghijklmnop1234" not in row["input_summary"]
    assert "[REDACTED]" in row["input_summary"]
    assert "hunter2seventeen" not in row["output_summary"]


def test_a_summary_cannot_grow_without_bound(store) -> None:
    """A tool can return megabytes; a telemetry column should not carry them."""
    h._record_tool_event(  # noqa: SLF001
        "sess-5", "Bash", {"tool_input": "x" * 50_000}, "y" * 50_000,
    )

    row = _rows(store)[0]
    assert len(row["input_summary"]) <= h._MAX_SUMMARY_LEN  # noqa: SLF001
    assert len(row["output_summary"]) <= h._MAX_SUMMARY_LEN  # noqa: SLF001


def test_telemetry_failure_is_never_the_tool_call_s_problem(
    tmp_path, monkeypatch,
) -> None:
    """No table, no database, no write -- and still no exception.

    The hook's contract is to exit 0 whatever happens. A tool call must not
    report a problem to the user because a telemetry row could not be stored.
    """
    monkeypatch.setattr(h, "_memory_db_path", lambda: tmp_path / "absent.db")

    assert h._record_tool_event(  # noqa: SLF001
        "sess-6", "Bash", {"tool_input": "x"}, "out",
    ) is False

