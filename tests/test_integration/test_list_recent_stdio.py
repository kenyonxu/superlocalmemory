# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""MCP stdio acceptance for list_recent per-request profile (spec section 6).

The spec's stdio-lane acceptance, verbatim: "SLM_MCP_TOOLS 含 list_recent
时全通,不含时不可见" — when the allowlist names list_recent the tool works
end to end (with profile_id); when it does not, the tool is invisible.

Exercised here against a REAL unified-daemon subprocess and a REAL
``mslm mcp`` stdio child over a full JSON-RPC round trip:

A. ``SLM_MCP_TOOLS=remember,recall,list_recent``: tools/list exposes exactly
   the three names, and a ``list_recent`` call carrying ``profile_id`` is
   routed — a doris-anchored list returns doris facts (complete untruncated
   content, importance, session_id) and never zhihui's, a zhihui-anchored
   list the mirror image; an empty but real namespace is a plain success
   with zero results; the daemon's global profile pointer and generation
   never move.
B. ``SLM_MCP_TOOLS=remember,recall`` (list_recent absent): tools/list
   exposes exactly the two names — list_recent is not visible.

Orchestration follows the established pattern of
``tests/test_integration/test_per_request_profile_e2e.py``: a real daemon
subprocess on a kernel-assigned ephemeral port with an isolated
``SLM_DATA_DIR``. The production daemon on 8765 is never touched — the
ephemeral port is reserved outside the public set, all child environments
are constructed (never inherited), and teardown proves machine state was
restored: the daemon process group is gone, its lifecycle files are
removed, the port is bindable again, and every foreign daemon PID that was
alive before the suite is still alive after it.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

# Ports owned by public/production daemons on this machine. Never bind, never
# connect — enforced by reserving outside this set (and the root conftest's
# audit hook denies them in-process as a second belt).
PRODUCTION_PORTS = {8765, 8767}
PROFILES = ("doris", "zhihui", "empty")

# Unique-per-run tokens keep every marker lexical hit attributable to THIS
# run even though one daemon serves the whole module.
RUN_TAG = uuid.uuid4().hex[:8]

# Well past the pre-fix truncation boundary (100 chars daemon / 120 chars
# MCP), so a truncated response can never accidentally equal the seed.
DORIS_CONTENT = (
    "LrStdio{tag}DorisReef maintains the harbor pilot rota and files the "
    "tide-window ledger for the northern approach, including the quarterly "
    "audit buffers for the on-call rotation."
)
ZHIHUI_CONTENT = (
    "LrStdio{tag}ZhihuiLark tunes the lantern festival drones and keeps the "
    "flight permits for the river parade, plus the rehearsal manifest for "
    "the drone swarm over the old mint."
)

_PROXY_VARS = (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
)
_PASSTHROUGH_VARS = ("PATH", "LANG", "LC_ALL", "TMPDIR", "TERM")


def _reserve_private_port() -> int:
    """Ask the kernel for a loopback port, never the public daemon ports."""
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        if port not in PRODUCTION_PORTS:
            return port
    raise AssertionError("could not reserve an isolated daemon port")


def _child_env(data_root: Path, port: int, home: Path, cache_root: Path) -> dict:
    """A constructed (never inherited) environment for SLM child processes.

    Everything identity-bearing is pinned inside the fixture-owned root:
    SLM_DATA_DIR (databases, locks, descriptor), HOME, and every model cache
    (offline so a cold cache can never trigger a network fetch). Proxy
    variables are stripped so loopback HTTP cannot be middle-boxed.
    """
    env = {name: os.environ[name] for name in _PASSTHROUGH_VARS if name in os.environ}
    env.update(
        {
            "HOME": str(home),
            "PYTHONPATH": str(SRC_ROOT),
            "SLM_DATA_DIR": str(data_root),
            "SLM_DAEMON_PORT": str(port),
            "OMP_NUM_THREADS": "1",
            "KMP_DUPLICATE_LIB_OK": "TRUE",
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HOME": str(cache_root / "huggingface"),
            "SENTENCE_TRANSFORMERS_HOME": str(cache_root / "sentence-transformers"),
            "XDG_CACHE_HOME": str(cache_root),
            "CI": "1",
            "SLM_NON_INTERACTIVE": "1",
            "SLM_TEST_ISOLATION": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    for var in _PROXY_VARS:
        env.pop(var, None)
    return env


def _foreign_daemon_pids() -> set[int]:
    """PIDs of unified daemons that do not belong to this test (production)."""
    try:
        import psutil
    except Exception:  # pragma: no cover — psutil is a test dependency
        return set()
    mine = os.getpid()
    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
        except Exception:
            continue
        if (
            proc.info["pid"] != mine
            and "superlocalmemory.server.unified_daemon" in cmdline
        ):
            pids.add(proc.info["pid"])
    return pids


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class _RpcClient:
    """Newline-delimited JSON-RPC driver for one ``mslm mcp`` stdio child."""

    def __init__(self, proc: subprocess.Popen, stderr_path: Path) -> None:
        self._proc = proc
        self._stderr_path = stderr_path
        self._responses: dict[object, dict] = {}
        self._next_id = 0
        self._reader = threading.Thread(target=self._read_forever, daemon=True)
        self._reader.start()

    def _read_forever(self) -> None:
        try:
            for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "id" in message:
                    self._responses[message["id"]] = message
        except Exception:  # reader must outlive the child silently
            pass

    def _stderr_tail(self) -> str:
        try:
            return self._stderr_path.read_text(
                encoding="utf-8", errors="replace",
            )[-1500:]
        except OSError:
            return "(stderr log unavailable)"

    def call(self, method: str, params: dict | None = None, *, timeout: float = 120.0) -> dict:
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self._proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if request_id in self._responses:
                return self._responses[request_id]
            if self._proc.poll() is not None:
                raise AssertionError(
                    f"mcp child exited rc={self._proc.returncode} during {method}; "
                    f"stderr tail:\n{self._stderr_tail()}"
                )
            time.sleep(0.05)
        raise AssertionError(
            f"timed out waiting for {method} response; stderr tail:\n"
            f"{self._stderr_tail()}"
        )

    def notify(self, method: str) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self._proc.stdin.flush()

    def close(self) -> None:
        """Close stdin (FastMCP exits on EOF), then escalate if needed."""
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=15)
            return
        except subprocess.TimeoutExpired:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=10)


class RealDaemon:
    """One real unified-daemon subprocess in a fixture-owned namespace."""

    def __init__(self, proc: subprocess.Popen, port: int, data_root: Path,
                 stdout_log: Path, env: dict) -> None:
        self.proc = proc
        self.port = port
        self.data_root = data_root
        self.stdout_log = stdout_log
        self.env = env

    # -- identity ---------------------------------------------------------

    @property
    def descriptor_path(self) -> Path:
        return self.data_root / "daemon.json"

    def descriptor(self) -> dict:
        return json.loads(self.descriptor_path.read_text(encoding="utf-8"))

    # -- HTTP -------------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None,
                params: dict | None = None, timeout: float = 90.0) -> tuple[int, dict]:
        """Authenticated loopback request using the daemon's own capability.

        Mirrors ``superlocalmemory.cli.daemon.daemon_request``: the
        descriptor in OUR data root carries the capability/instance headers
        the write routes require.
        """
        descriptor = self.descriptor()
        url = f"http://127.0.0.1:{self.port}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        headers["X-SLM-Daemon-Capability"] = descriptor["capability"]
        headers["X-SLM-Target-Instance"] = descriptor["instance_id"]
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(payload)
            except ValueError:
                return exc.code, {"raw": payload}

    def status(self) -> dict:
        code, payload = self.request("GET", "/status")
        assert code == 200, payload
        return payload

    def remember(self, content: str, profile_id: str, idempotency_key: str,
                 session_id: str = "") -> dict:
        body = {"content": content, "idempotency_key": idempotency_key}
        if profile_id:
            body["profile_id"] = profile_id
        if session_id:
            body["session_id"] = session_id
        code, payload = self.request("POST", "/remember", body)
        assert code == 200, payload
        assert payload.get("ok") is True, payload
        return payload

    # -- lifecycle --------------------------------------------------------

    def _log_tail(self) -> str:
        chunks = []
        for path in (self.stdout_log, self.data_root / "logs" / "daemon.log"):
            try:
                chunks.append(
                    f"--- {path} ---\n"
                    + path.read_text(encoding="utf-8", errors="replace")[-2500:]
                )
            except OSError:
                continue
        return "\n".join(chunks) or "(no daemon logs available)"

    def wait_ready(self, timeout: float = 300.0) -> None:
        """Wait until /status answers 200 (engine serving requests).

        /health is deliberately NOT the readiness bar here: it embeds a
        channel-health probe that can trail engine readiness by minutes on a
        cold, offline-model sandbox. /status is the daemon's own
        non-blocking "serving" answer.
        """
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"daemon exited rc={self.proc.returncode} during startup;\n"
                    f"{self._log_tail()}"
                )
            try:
                code, _ = self.request("GET", "/status", timeout=5)
                if code == 200:
                    return
            except Exception as exc:  # not listening yet / descriptor missing
                last_error = exc
            time.sleep(0.5)
        raise AssertionError(
            f"daemon not ready within {timeout}s (last error: {last_error!r});\n"
            f"{self._log_tail()}"
        )

    def wait_health_fast(self, timeout: float = 300.0) -> None:
        """Wait until /health answers 200 within daemon_request's 2s bar.

        The MCP tool lane preflights /health with a 2-second timeout before
        every daemon call; only a fast answer proves the child will route
        through this daemon instead of reporting DAEMON_UNAVAILABLE.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/health", timeout=2,
                ) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass
            time.sleep(2.0)
        raise AssertionError(
            "/health never answered within the 2s daemon_request preflight "
            f"bar after {timeout}s;\n" + self._log_tail()
        )

    def precreate_profiles(self, profiles: tuple[str, ...]) -> None:
        """Insert profiles table rows the way the server tests do.

        A routed read must find its profile already present (routing never
        implicitly creates one), so the profiles are seeded by hand before
        any client talks to the daemon. WAL + busy_timeout lets this short
        write land while the daemon holds the database.
        """
        conn = sqlite3.connect(self.data_root / "memory.db", timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            for profile_id in profiles:
                conn.execute(
                    "INSERT OR IGNORE INTO profiles (profile_id, name) "
                    "VALUES (?, ?)",
                    (profile_id, f"Stdio Profile {profile_id}"),
                )
            conn.commit()
        finally:
            conn.close()
        # Prove the daemon sees the rows: a routed list of a seeded profile
        # must be a normal 200, not the unknown_profile 404.
        for profile_id in profiles:
            code, payload = self.request(
                "GET", "/list", params={"profile_id": profile_id},
            )
            assert code == 200, payload

    def _group_members(self) -> list[str]:
        """Live processes still in the daemon's process group."""
        listing = subprocess.run(
            ["ps", "-eo", "pid,pgid,args"],
            capture_output=True, text=True, timeout=30,
        ).stdout.splitlines()
        return [
            line for line in listing
            if line.split() and line.split()[1] == str(self.proc.pid)
        ]

    def stop(self, foreign_before: set[int]) -> None:
        """Stop the daemon and PROVE machine state was restored."""
        # 1. Graceful stop via the daemon's own capability-bound route.
        try:
            self.request("POST", "/stop", body={}, timeout=10)
        except Exception:
            pass  # escalate below
        graceful = True
        try:
            self.proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            graceful = False
            if os.name == "posix":
                os.killpg(self.proc.pid, signal.SIGTERM)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(self.proc.pid, signal.SIGKILL)
                else:
                    self.proc.kill()
                self.proc.wait(timeout=20)
        # 2. No member of the daemon's process group survives (workers
        #    included — they were spawned into the same session). Workers
        #    self-terminate on a ~10s parent-watchdog poll after the daemon
        #    exits, so grant that grace, then force, then assert.
        if os.name == "posix":
            deadline = time.monotonic() + 30
            leaked = self._group_members()
            while leaked and time.monotonic() < deadline:
                time.sleep(1.0)
                leaked = self._group_members()
            if leaked:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                time.sleep(2.0)
                leaked = self._group_members()
            assert leaked == [], (
                f"daemon process group {self.proc.pid} leaked members: {leaked}"
            )

        # 3. Graceful stop removes exactly the ephemeral lifecycle identity.
        if graceful:
            for name in ("daemon.json", "daemon.pid", "daemon.port"):
                assert not (self.data_root / name).exists(), (
                    f"stale lifecycle state survived stop: {name}"
                )

        # 4. The ephemeral port is bindable again.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", self.port))

        # 5. The production daemon was never touched: every foreign daemon
        #    PID observed before this suite is still alive.
        still_alive = {pid for pid in foreign_before if _alive(pid)}
        assert still_alive == foreign_before, (
            f"foreign daemons changed during the suite: "
            f"before={sorted(foreign_before)} after={sorted(still_alive)}"
        )


@pytest.fixture(scope="module")
def real_daemon(tmp_path_factory):
    """The REAL daemon subprocess shared by every stdio scenario below."""
    root = tmp_path_factory.mktemp("lr-stdio")
    data_root = root / "data"
    data_root.mkdir()
    port = _reserve_private_port()
    assert port not in PRODUCTION_PORTS
    env = _child_env(data_root, port, root / "home", root / "cache")

    foreign_before = _foreign_daemon_pids()

    stdout_log = root / "daemon-stdout.log"
    with stdout_log.open("wb") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "superlocalmemory.server.unified_daemon",
             "--start", f"--port={port}"],
            stdout=log_file, stderr=log_file, env=env, cwd=str(REPO_ROOT),
            start_new_session=os.name == "posix",
        )
    daemon = RealDaemon(proc, port, data_root, stdout_log, env)
    try:
        daemon.wait_ready()
        daemon.precreate_profiles(PROFILES)
        yield daemon
    finally:
        daemon.stop(foreign_before)


def _spawn_mcp_child(real_daemon, tools: str, name: str) -> tuple:
    """One ``mslm mcp`` stdio child with the given SLM_MCP_TOOLS allowlist."""
    env = dict(real_daemon.env)
    env.update(
        {
            "SLM_MCP_TOOLS": tools,
            # Skip the ensure_daemon warmup thread: the descriptor in our
            # isolated root already names the test daemon, and the test must
            # never auto-start anything else.
            "SLM_DISABLE_WARMUP_SIDE_EFFECTS": "1",
        }
    )
    stderr_path = real_daemon.data_root.parent / f"mcp-stderr-{name}.log"
    with stderr_path.open("wb") as stderr_log:
        mcp = subprocess.Popen(
            [sys.executable, "-m", "superlocalmemory.cli.main", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr_log, env=env, cwd=str(REPO_ROOT),
        )
    return mcp, stderr_path


def _handshake(client: _RpcClient) -> None:
    """The full handshake a real IDE performs over stdio."""
    initialized = client.call("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "lr-stdio-e2e", "version": "1.0"},
    })
    assert "result" in initialized, initialized
    client.notify("notifications/initialized")


def _tool_payload(result: dict) -> dict:
    assert result.get("result", {}).get("isError") is not True, result
    return json.loads(result["result"]["content"][0]["text"])


class TestListRecentStdioRouted:
    """Spec stdio acceptance, first half: allowlist contains list_recent."""

    def test_tools_list_exposes_exactly_the_allowlisted_trio(self, real_daemon):
        mcp, stderr_path = _spawn_mcp_child(
            real_daemon, "remember,recall,list_recent", "routed",
        )
        client = _RpcClient(mcp, stderr_path)
        try:
            _handshake(client)
            listed = client.call("tools/list", {})
            tool_names = {tool["name"] for tool in listed["result"]["tools"]}
            assert tool_names == {"remember", "recall", "list_recent"}, tool_names
        finally:
            client.close()
            assert mcp.poll() is not None, "mcp child must exit after stdin EOF"

    def test_list_recent_with_profile_id_is_routed_and_complete(
        self, real_daemon,
    ):
        # daemon_request preflights /health with a 2s timeout; only a fast
        # answer lets the child route through THIS daemon (a slow one makes
        # the tool return DAEMON_UNAVAILABLE instead of silently falling
        # back, so success below proves daemon routing).
        real_daemon.wait_health_fast()

        doris_content = DORIS_CONTENT.format(tag=RUN_TAG)
        zhihui_content = ZHIHUI_CONTENT.format(tag=RUN_TAG)
        assert len(doris_content) > 120, (
            "seed must exceed the pre-fix truncation boundary or the "
            "completeness assertion below would pass vacuously"
        )

        # Seed both namespaces through the daemon's own HTTP write route.
        real_daemon.remember(
            doris_content, profile_id="doris",
            idempotency_key=f"{RUN_TAG}-lr-d1", session_id="sess-lr-stdio-d1",
        )
        real_daemon.remember(
            zhihui_content, profile_id="zhihui",
            idempotency_key=f"{RUN_TAG}-lr-z1", session_id="sess-lr-stdio-z1",
        )
        status_before = real_daemon.status()

        mcp, stderr_path = _spawn_mcp_child(
            real_daemon, "remember,recall,list_recent", "routed-call",
        )
        client = _RpcClient(mcp, stderr_path)
        try:
            _handshake(client)

            # (a) doris-anchored list: routed, complete, cross-invisible.
            doris = _tool_payload(client.call("tools/call", {
                "name": "list_recent",
                "arguments": {"profile_id": "doris", "limit": 10},
            }))
            assert doris["success"] is True, doris
            # Envelope honesty: echo the profile the read was served from.
            assert doris["profile"] == "doris", doris
            assert doris["count"] == len(doris["results"]) >= 1, doris
            hit = next(
                (item for item in doris["results"]
                 if item.get("content") == doris_content),
                None,
            )
            assert hit is not None, (
                f"doris-routed list must return the complete untruncated "
                f"content: {doris}"
            )
            assert hit["fact_id"]
            assert hit["fact_type"]
            assert hit["created_at"]
            assert "importance" in hit
            assert hit["session_id"] == "sess-lr-stdio-d1", hit
            assert not any(
                "ZhihuiLark" in str(item.get("content", ""))
                for item in doris["results"]
            ), f"zhihui memory leaked into the doris namespace: {doris}"

            # (b) zhihui-anchored list: the mirror image.
            zhihui = _tool_payload(client.call("tools/call", {
                "name": "list_recent",
                "arguments": {"profile_id": "zhihui", "limit": 10},
            }))
            assert zhihui["success"] is True, zhihui
            assert zhihui["profile"] == "zhihui", zhihui
            assert any(
                item.get("content") == zhihui_content
                for item in zhihui["results"]
            ), f"zhihui-routed list must hit the zhihui namespace: {zhihui}"
            assert not any(
                "DorisReef" in str(item.get("content", ""))
                for item in zhihui["results"]
            ), f"doris memory leaked into the zhihui namespace: {zhihui}"

            # Empty but real namespace: plain success, zero results, no
            # abstain — over the full stdio round trip.
            empty = _tool_payload(client.call("tools/call", {
                "name": "list_recent",
                "arguments": {"profile_id": "empty", "limit": 10},
            }))
            assert empty["success"] is True, empty
            assert empty["results"] == [], empty
            assert empty["count"] == 0, empty
            assert "abstain" not in empty, empty

            # Legacy call (no profile_id) still works, byte-compatible:
            # it lands on the daemon's active profile.
            legacy = _tool_payload(client.call("tools/call", {
                "name": "list_recent",
                "arguments": {"limit": 10},
            }))
            assert legacy["success"] is True, legacy
            assert legacy["profile"] == status_before["profile"], legacy

            # The global pointer survived the whole stdio session.
            status_after = real_daemon.status()
            assert status_after["profile"] == status_before["profile"]
            assert (
                status_after["profile_generation"]
                == status_before["profile_generation"]
            )
        finally:
            client.close()
            assert mcp.poll() is not None, "mcp child must exit after stdin EOF"


class TestListRecentAbsentFromAllowlist:
    """Spec stdio acceptance, second half: not allowlisted means invisible."""

    def test_tools_list_omits_list_recent_when_not_allowlisted(self, real_daemon):
        mcp, stderr_path = _spawn_mcp_child(
            real_daemon, "remember,recall", "absent",
        )
        client = _RpcClient(mcp, stderr_path)
        try:
            _handshake(client)
            listed = client.call("tools/list", {})
            tool_names = {tool["name"] for tool in listed["result"]["tools"]}
            assert tool_names == {"remember", "recall"}, tool_names
            assert "list_recent" not in tool_names
        finally:
            client.close()
            assert mcp.poll() is not None, "mcp child must exit after stdin EOF"
