"""Live MCP sessions, never installed config files, establish connectivity."""

from types import SimpleNamespace

import pytest

from superlocalmemory.infra import mcp_processes as registry


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "process_identity", lambda pid: f"start-{pid}")
    return tmp_path


def test_live_sessions_are_grouped_and_versioned(live):
    registry.register(live, "one", pid=101, host="codex", version="4.1.24", tools=["recall"])
    registry.register(live, "two", pid=102, host="codex", version="4.1.25", tools=["recall"])
    registry.register(live, "three", pid=103, host="hermes", version="4.1.25", tools=["recall"])
    view = registry.snapshot(live, installed_version="4.1.25")
    assert view["count"] == 3
    assert view["stale_count"] == 1
    assert {h["host"]: h["count"] for h in view["hosts"]} == {"codex": 2, "hermes": 1}
    assert view["apps"][0]["code_version"] == "4.1.24"


def test_dead_and_reused_pids_are_not_connected(live, monkeypatch):
    registry.register(live, "dead", pid=101, host="cursor", version="4.1.25", tools=[])
    registry.register(live, "reused", pid=102, host="hermes", version="4.1.25", tools=[])
    monkeypatch.setattr(
        registry, "process_identity", lambda pid: None if pid == 101 else "new-instance"
    )
    assert registry.snapshot(live, installed_version="4.1.25")["count"] == 0


def test_reconnect_is_bound_to_live_session_and_root(live):
    registry.register(live, "one", pid=101, host="codex", version="old", tools=[])
    assert registry.request_reconnect(live, "one") is True
    assert registry.reconnect_requested(live, "one") is True
    assert registry.request_reconnect(live / "other", "one") is False
    registry.unregister(live, "one")
    assert registry.snapshot(live)["count"] == 0


def test_untrusted_record_and_traversal_cannot_choose_a_process(live):
    with pytest.raises(ValueError):
        registry.request_reconnect(live, "../one")
    directory = live / "mcp-processes"
    directory.mkdir()
    (directory / "broken.json").write_text('{"pid": "wrong"}')
    assert registry.snapshot(live)["count"] == 0


@pytest.mark.parametrize(
    "name,host",
    [
        ("Claude Code", "claude-code"),
        ("Claude Desktop", "claude-desktop"),
        ("codex-mcp-client", "codex"),
        ("Hermes", "hermes"),
        ("Antigravity", "antigravity"),
        ("Cursor", "cursor"),
    ],
)
def test_client_handshake_names_identify_the_host(name, host):
    params = SimpleNamespace(client_info=SimpleNamespace(name=name))
    assert registry.host_from_client(params, ancestors=[]) == host
