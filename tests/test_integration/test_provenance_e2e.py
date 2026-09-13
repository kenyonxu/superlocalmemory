# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""End-to-end acceptance for provenance_kind over a REAL daemon (spec section 6).

Task-5 acceptance scenarios (task brief, steps 1-2):

1. ``remember(provenance_kind="world")`` -> the governance tag and scope echo
   back on THREE real read surfaces: canonical ``/recall``, the dashboard
   ``/api/search`` (both serialized through the recall chokepoint), and the
   daemon ``/list``.
2. personal -> global scope migration + ``curated`` tag in place, then the
   Step-1 audit's end-to-end assertion: a recall from ANOTHER profile with
   ``include_global=true`` must FIND the migrated fact (cross-profile proof
   that no cache serves a stale scope slot), while a same-profile read echoes
   the migrated scope + tag.
3. dual-profile routed curation: a PATCH with ``?profile_id=`` revises only
   the routed profile's fact; the other profile's copy keeps its state and
   the daemon's active-profile pointer (and its generation) never move.
4. curation scan over the real daemon: ``/list?scope=global&
   provenance_kind=null`` returns EXACTLY the untagged global rows (and the
   neighbouring filters return exactly theirs).

Orchestration follows ``tests/test_integration/test_per_request_profile_e2e.py``
exactly: a REAL unified-daemon subprocess on a kernel-assigned ephemeral port
with an isolated ``SLM_DATA_DIR``. The production daemon on 8765 is never
touched; teardown proves machine state was restored.

Scope-migration cache audit backing scenario 2 (task-5 report step 1):
- adjacency cache slots are keyed (profile, include_global, include_shared)
  and re-COUNT live rows on every use, so a scope flip changes the fact count
  of every affected slot and forces a reload regardless of TTL;
- the sqlite-vec index is partitioned by OWNER profile (migration does not
  move the owner) and cross-profile candidates are merged through a live
  ``get_external_visible_facts`` query per search;
- BM25's FTS5 path joins ``atomic_facts`` through the canonical scope
  predicate on every query.
Scenario 2 asserts the composed end-to-end behaviour: migrated fact found
under ``include_global=true`` from another profile immediately after the
in-place curation write, with no cache invalidation call anywhere.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
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
# connect — enforced by reserving outside this set.
PRODUCTION_PORTS = {8765, 8767}
# mira curates; teo is the second namespace the migrated fact must reach.
PROFILES = ("mira", "teo")

# Unique-per-run tokens keep every lexical hit attributable to THIS run even
# though one daemon serves the whole module.
RUN_TAG = uuid.uuid4().hex[:8]

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
    """A constructed (never inherited) environment for the daemon child.

    Everything identity-bearing is pinned inside the fixture-owned root:
    SLM_DATA_DIR, HOME, and every model cache (offline so a cold cache can
    never trigger a network fetch). Proxy variables are stripped so loopback
    HTTP cannot be middle-boxed.
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
                params: dict | None = None, timeout: float = 120.0) -> tuple[int, dict]:
        """Authenticated loopback request using the daemon's own capability."""
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

    def remember(self, content: str, *, profile_id: str = "",
                 provenance_kind: str = "", scope: str = "",
                 idempotency_key: str = "", wait: bool = True) -> dict:
        body = {"content": content}
        params: dict = {}
        if profile_id:
            body["profile_id"] = profile_id
        if provenance_kind:
            body["provenance_kind"] = provenance_kind
        if scope:
            body["scope"] = scope
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        if wait:
            params["wait"] = "true"
        code, payload = self.request(
            "POST", "/remember", body, params=params or None,
        )
        assert code == 200, payload
        assert payload.get("ok") is True, payload
        return payload

    def recall(self, query: str, *, profile_id: str = "",
               include_global: bool | None = None) -> dict:
        params: dict = {"q": query}
        if profile_id:
            params["profile_id"] = profile_id
        if include_global is not None:
            params["include_global"] = "true" if include_global else "false"
        code, payload = self.request("GET", "/recall", params=params)
        assert code == 200, payload
        return payload

    def list_facts(self, *, profile_id: str = "", scope: str = "",
                   provenance_kind: str = "", limit: int = 200) -> dict:
        params: dict = {"limit": limit}
        if profile_id:
            params["profile_id"] = profile_id
        if scope:
            params["scope"] = scope
        if provenance_kind:
            params["provenance_kind"] = provenance_kind
        code, payload = self.request("GET", "/list", params=params)
        assert code == 200, payload
        return payload

    def curate(self, fact_id: str, body: dict, *, profile_id: str = "") -> tuple[int, dict]:
        params = {"profile_id": profile_id} if profile_id else None
        return self.request("PATCH", f"/api/memories/{fact_id}", body, params=params)

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
        """Wait until /status answers 200 (engine serving requests)."""
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

    def precreate_profiles(self, profiles: tuple[str, ...]) -> None:
        """Insert profiles table rows the way the server tests do.

        A routed write must find its profile already present (routing never
        implicitly creates one), so mira/teo are seeded by hand before any
        client talks to the daemon. WAL + busy_timeout lets this short write
        land while the daemon holds the database.
        """
        conn = sqlite3.connect(self.data_root / "memory.db", timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            for profile_id in profiles:
                conn.execute(
                    "INSERT OR IGNORE INTO profiles (profile_id, name) "
                    "VALUES (?, ?)",
                    (profile_id, f"E2E Profile {profile_id}"),
                )
            conn.commit()
        finally:
            conn.close()
        # Prove the daemon sees the rows: a routed recall of a seeded profile
        # must be a normal 200, not the unknown_profile 404.
        for profile_id in profiles:
            code, payload = self.request(
                "GET", "/recall",
                params={"q": "seeded-profile-probe", "profile_id": profile_id},
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
        # 2. No member of the daemon's process group survives.
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

        # 5. The production daemon was never touched.
        still_alive = {pid for pid in foreign_before if _alive(pid)}
        assert still_alive == foreign_before, (
            f"foreign daemons changed during the suite: "
            f"before={sorted(foreign_before)} after={sorted(still_alive)}"
        )


@pytest.fixture(scope="module")
def real_daemon(tmp_path_factory):
    """The REAL daemon subprocess shared by every acceptance scenario below."""
    root = tmp_path_factory.mktemp("prov-e2e")
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


def _results(payload: dict) -> list[dict]:
    return list(payload.get("results", []))


def _contents(payload: dict) -> list[str]:
    return [str(item.get("content", "")) for item in payload.get("results", [])]


def _by_token(items: list[dict], token: str) -> list[dict]:
    return [item for item in items if token in str(item.get("content", ""))]


class TestAcceptance1TagEchoesOnThreeReadSurfaces:
    """Scenario 1: remember(world) -> recall, dashboard search, and list echo."""

    def test_world_tag_and_scope_echo_on_reads(self, real_daemon):
        token = f"ProvE2e{RUN_TAG}LanternLedger"
        receipt = real_daemon.remember(
            f"{token} records the beacon calibration rota kept at the "
            "northern breakwater.",
            provenance_kind="world",
            idempotency_key=f"{RUN_TAG}-echo-world-1",
        )
        assert receipt.get("fact_ids"), receipt
        fact_id = receipt["fact_ids"][0]

        # Read 1: canonical /recall (active profile — the write was unrouted).
        recall = real_daemon.recall(f"{token} beacon calibration")
        match = next(
            (item for item in _results(recall) if item.get("fact_id") == fact_id),
            None,
        )
        assert match is not None, recall
        assert match["scope"] == "personal"
        assert match["provenance_kind"] == "world"

        # Read 2: dashboard /api/search (same recall serializer chokepoint).
        code, search = real_daemon.request(
            "POST", "/api/search", {"query": f"{token} beacon calibration", "limit": 10},
        )
        assert code == 200, search
        match = next(
            (item for item in _results(search) if item.get("fact_id") == fact_id),
            None,
        )
        assert match is not None, search
        assert match["scope"] == "personal"
        assert match["provenance_kind"] == "world"

        # Read 3: daemon /list.
        listed = real_daemon.list_facts()
        match = next(
            (item for item in _results(listed) if item.get("fact_id") == fact_id),
            None,
        )
        assert match is not None, listed
        assert match["scope"] == "personal"
        assert match["provenance_kind"] == "world"


class TestAcceptance2ScopeMigrationFoundUnderIncludeGlobal:
    """Scenario 2: personal -> global migration is immediately recallable
    cross-profile with include_global=true — the Step-1 audit's end-to-end
    assertion. No cache invalidation is issued anywhere between the curation
    write and the recall; if any cached scope slot could go stale, this test
    is where it surfaces."""

    def test_migrated_fact_reaches_other_profile_recall(self, real_daemon):
        token = f"ProvE2e{RUN_TAG}AureliaReef"
        receipt = real_daemon.remember(
            f"{token} maintains the pilot ledger for Aurelia at the northern "
            "reef approach.",
            profile_id="mira",
            provenance_kind="world",
            idempotency_key=f"{RUN_TAG}-mig-1",
        )
        fact_id = receipt["fact_ids"][0]

        # Before migration: teo must NOT see mira's personal fact even when
        # opting into global scope.
        before = real_daemon.recall(
            f"{token} Aurelia pilot ledger",
            profile_id="teo", include_global=True,
        )
        assert not any(token in c for c in _contents(before)), before

        # In-place curation revision: migrate scope + re-tag, fact_id stable.
        code, curated = real_daemon.curate(
            fact_id,
            {"scope": "global", "provenance_kind": "curated"},
            profile_id="mira",
        )
        assert code == 200, curated
        assert curated.get("in_place") is True, curated
        assert curated.get("scope") == "global"
        assert curated.get("provenance_kind") == "curated"

        # THE audit assertion: teo's include_global recall FINDS the migrated
        # fact immediately (live scope predicates + count-based cache reload).
        after = real_daemon.recall(
            f"{token} Aurelia pilot ledger",
            profile_id="teo", include_global=True,
        )
        hits = _by_token(_results(after), token)
        assert hits, after
        assert hits[0]["fact_id"] == fact_id
        assert hits[0]["scope"] == "global"
        assert hits[0]["provenance_kind"] == "curated"

        # teo's private recall (no include_global) still must NOT see it.
        private = real_daemon.recall(
            f"{token} Aurelia pilot ledger",
            profile_id="teo", include_global=False,
        )
        assert not any(token in c for c in _contents(private)), private

        # The owner's own read echoes the migrated state in place.
        owner = real_daemon.recall(
            f"{token} Aurelia pilot ledger", profile_id="mira",
        )
        owner_hit = next(
            (item for item in _results(owner) if item.get("fact_id") == fact_id),
            None,
        )
        assert owner_hit is not None, owner
        assert owner_hit["scope"] == "global"
        assert owner_hit["provenance_kind"] == "curated"


class TestAcceptance3RoutedCurationIsScopedAndPointerFrozen:
    """Scenario 3: a routed curation revises only the routed profile's fact;
    the daemon's active profile and its generation never move."""

    def test_routed_curation_touches_only_its_profile(self, real_daemon):
        token = f"ProvE2e{RUN_TAG}TideRota"
        mira_write = real_daemon.remember(
            f"{token} for the mira quay: spring tide windows filed weekly.",
            profile_id="mira",
            idempotency_key=f"{RUN_TAG}-dual-mira",
        )
        teo_write = real_daemon.remember(
            f"{token} for the teo quay: neap tide windows filed monthly.",
            profile_id="teo",
            idempotency_key=f"{RUN_TAG}-dual-teo",
        )
        mira_fact = mira_write["fact_ids"][0]
        teo_fact = teo_write["fact_ids"][0]

        status_before = real_daemon.status()

        # Routed curation: tag ONLY mira's copy (curation-only body routes).
        code, curated = real_daemon.curate(
            mira_fact,
            {"provenance_kind": "private"},
            profile_id="mira",
        )
        assert code == 200, curated

        # The pointer never moved.
        status_after = real_daemon.status()
        assert status_after.get("profile") == status_before.get("profile")
        assert (
            status_after.get("profile_generation")
            == status_before.get("profile_generation")
        )

        # mira's copy is tagged; teo's copy is untouched (still untagged,
        # still personal) — same token, different namespaces.
        mira_list = _by_token(
            _results(real_daemon.list_facts(profile_id="mira")), token,
        )
        teo_list = _by_token(
            _results(real_daemon.list_facts(profile_id="teo")), token,
        )
        assert len(mira_list) == 1 and len(teo_list) == 1
        assert mira_list[0]["fact_id"] == mira_fact
        assert mira_list[0]["provenance_kind"] == "private"
        assert teo_list[0]["fact_id"] == teo_fact
        assert teo_list[0]["provenance_kind"] is None
        assert teo_list[0]["scope"] == "personal"

        # A foreign fact_id under a routed curation is a clean 404, never a
        # cross-profile write: teo's fact_id addressed via mira.
        code, foreign = real_daemon.curate(
            teo_fact, {"provenance_kind": "legacy"}, profile_id="mira",
        )
        assert code == 404, foreign
        teo_after = _by_token(
            _results(real_daemon.list_facts(profile_id="teo")), token,
        )
        assert teo_after[0]["provenance_kind"] is None


class TestAcceptance4CurationScanExactCounts:
    """Scenario 4: the curation scan returns EXACTLY the addressed rows.

    Seeds (mira): world/global, curated/global, untagged/global, and an
    untagged/personal control that must never leak into a scope=global scan.
    """

    def test_scan_filters_return_exact_sets(self, real_daemon):
        token = f"ProvE2e{RUN_TAG}ScanSet"
        seeds = (
            ("world", "global", "world"),
            ("curated", "global", "curated"),
            ("untagged", "global", ""),
            ("personal-ctrl", "personal", ""),
        )
        ids: dict[str, str] = {}
        for name, scope, kind in seeds:
            receipt = real_daemon.remember(
                f"{token} {name} dredging record filed for the curation "
                "scan fixture.",
                profile_id="mira",
                scope=scope,
                provenance_kind=kind,
                idempotency_key=f"{RUN_TAG}-scan-{name}",
            )
            ids[name] = receipt["fact_ids"][0]

        def scan(**params) -> list[dict]:
            return _by_token(
                _results(real_daemon.list_facts(profile_id="mira", **params)),
                token,
            )

        # scope=global + provenance_kind=null -> exactly the untagged global row.
        null_hits = scan(scope="global", provenance_kind="null")
        assert {item["fact_id"] for item in null_hits} == {ids["untagged"]}, (
            null_hits
        )
        assert null_hits[0]["provenance_kind"] is None

        # scope=global + provenance_kind=world -> exactly the world row.
        world_hits = scan(scope="global", provenance_kind="world")
        assert {item["fact_id"] for item in world_hits} == {ids["world"]}

        # scope=global alone -> the three global rows, never the personal one.
        global_hits = scan(scope="global")
        assert {item["fact_id"] for item in global_hits} == {
            ids["world"], ids["curated"], ids["untagged"],
        }, global_hits

        # No filter -> all four (ownership arm keeps the personal row).
        all_hits = scan()
        assert {item["fact_id"] for item in all_hits} == set(ids.values())

        # Out-of-vocabulary scan values are a 400, never an empty page.
        code, rejected = real_daemon.request(
            "GET", "/list",
            params={
                "profile_id": "mira",
                "scope": "global",
                "provenance_kind": "galactic",
            },
        )
        assert code == 400, rejected
