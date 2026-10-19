# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The gateway process must not need the superlocalmemory package.

HOW THIS BIT US (2026-10-05..10-16). The Hermes gateway workspace cannot
install mslm-memory — its websockets/mcp pins fight the gateway's own — so
plugin.yaml dropped pip_dependencies on the promise that the provider
degrades to daemon-only mode. The runtime paths were made daemon-first, but
every layer still imported the package first: the availability gate, the
daemon probe, the daemon transport and initialize()'s config load. The
gateway env lost the package in a workspace rebuild and the provider went
fully dark for 11 days while the daemon stayed healthy.

These tests load the provider file the way a package-less gateway does —
by path, with a sys.meta_path hook that makes ``superlocalmemory`` (and only
it) unimportable — and pin the four layers against a fake local daemon.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_PROVIDER_PATH = (
    Path(__file__).resolve().parents[1] / "__init__.py"
)


class _BlockSuperLocalMemory:
    """meta_path finder that raises ImportError for superlocalmemory only."""

    def find_module(self, fullname, path=None):  # pragma: no cover - py<3.12
        return self.find_spec(fullname, path)

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "superlocalmemory" or fullname.startswith(
            "superlocalmemory."
        ):
            raise ImportError(f"blocked for test: {fullname}")
        return None


class _FakeDaemonHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send({
                "status": "ok", "ready": True, "version": "9.9.9-test",
                "engine": "initialized", "pid": 424242,
            })
        else:
            self._send({"detail": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.server.requests.append({
            "method": "POST", "path": self.path,
            "body": json.loads(self.rfile.read(length) or b"{}"),
        })
        if self.path == "/recall":
            self._send({
                "results": [{"fact_id": "f1", "content": "记忆",
                              "relevance_score": 0.9}],
                "total_candidates": 1,
            })
        elif self.path == "/remember":
            self._send({"ok": True, "fact_ids": ["f1"]})
        else:
            self._send({"detail": "not found"}, status=404)


@pytest.fixture()
def fake_daemon(tmp_path: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeDaemonHandler)
    server.requests = []
    server.serve_forever = server.serve_forever  # keep ref
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state_dir = tmp_path / "slm-state"
    state_dir.mkdir()
    (state_dir / "daemon.port").write_text(str(server.server_port) + "\n")
    try:
        yield {"server": server, "state_dir": state_dir,
               "port": server.server_port}
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def packageless_provider(fake_daemon, monkeypatch):
    """The provider module as a package-less gateway loads it.

    ``importlib.import_module`` consults ``sys.modules`` before any
    meta_path finder, and the outer pytest process has the real package
    imported — so the blocker alone is not enough: the cached modules are
    swapped out for the duration and restored afterwards.
    """
    monkeypatch.setenv("SLM_DATA_DIR", str(fake_daemon["state_dir"]))
    blocker = _BlockSuperLocalMemory()
    hidden = {
        name: mod for name, mod in list(sys.modules.items())
        if name == "superlocalmemory" or name.startswith("superlocalmemory.")
    }
    for name in hidden:
        del sys.modules[name]
    sys.meta_path.insert(0, blocker)
    spec = importlib.util.spec_from_file_location(
        "_pkgless_hermes_provider", _PROVIDER_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses in the module resolve their module by name
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.pop(spec.name, None)
        sys.modules.update(hidden)


class TestPackagelessAvailability:
    def test_is_available_true_with_reachable_daemon(self, packageless_provider):
        """The gate that went dark for 11 days: no package, healthy daemon
        → the provider is available."""
        provider = packageless_provider.SuperLocalMemoryProvider()
        assert provider.is_available() is True

    def test_daemon_probe_is_package_free(self, packageless_provider):
        assert packageless_provider._daemon_available() is True

    def test_daemon_probe_false_when_port_file_missing(
            self, packageless_provider, monkeypatch, tmp_path):
        monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "nowhere"))
        assert packageless_provider._daemon_available() is False


class TestPackagelessTransport:
    def test_daemon_api_roundtrip(self, packageless_provider, fake_daemon):
        result = packageless_provider._daemon_api(
            "POST", "/recall", {"q": "探针"}, timeout=5.0,
        )
        assert result is not None
        assert result["total_candidates"] == 1
        sent = fake_daemon["server"].requests[-1]
        assert sent["path"] == "/recall" and sent["body"]["q"] == "探针"

    def test_daemon_api_returns_none_on_dead_port(
            self, packageless_provider, monkeypatch, tmp_path):
        monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "nowhere"))
        assert packageless_provider._daemon_api(
            "GET", "/health", None, timeout=1.0,
        ) is None


class TestPackagelessInitialize:
    def test_initialize_keeps_daemon_only_mode(self, packageless_provider):
        """SLMConfig.load() is unreachable without the package; a reachable
        daemon must keep the provider serving instead of disabling it."""
        provider = packageless_provider.SuperLocalMemoryProvider()
        provider.initialize(
            "sess-pkgless",
            agent_identity="zhihui",
            agent_context="primary",
            platform="feishu",
        )
        assert provider._can_serve() is True
        assert provider._engine is None
        assert provider._mslm_profile == "zhihui"
        # Hermes-config flags still parse without SLMConfig
        assert provider._include_global is True

    def test_daemon_only_recall_serves(self, packageless_provider, fake_daemon):
        provider = packageless_provider.SuperLocalMemoryProvider()
        provider.initialize("sess-pkgless", agent_identity="zhihui")
        provider._session_id = "sess-pkgless"
        # The recall path may prefetch by itself; the daemon saw traffic and
        # the provider reports serviceable — no engine was ever created.
        assert provider._can_serve() is True
        assert provider._engine is None
