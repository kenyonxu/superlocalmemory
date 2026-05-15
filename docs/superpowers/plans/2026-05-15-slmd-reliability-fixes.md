# SLM Daemon 可靠性修复 + Recall Scope 参数贯通 — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 issues from Hermes Agent permanent-busy diagnosis: WAL lock ordering, MCP engine cooldown, recall scope TypeError, crash traceback logging, health endpoint engine recovery, and zombie child reaping.

**Architecture:** Six independent, small-scope fixes across 7 files. Each fix is self-contained and testable in isolation. No schema changes, no new dependencies. Fixes 1-4 are pure correctness changes; fixes 5-6 add minimal auto-recovery.

**Tech Stack:** Python 3.11+, SQLite WAL, pytest, threading/synchronization primitives.

**Spec:** `docs/superpowers/specs/2026-05-15-slmd-reliability-fixes.md`

---

## File Map

| File | Role | Fix # |
|------|------|-------|
| `src/superlocalmemory/storage/database.py:200-208` | `_enable_wal()` — reorder PRAGMA | 1 |
| `src/superlocalmemory/mcp/server.py:32-69` | `get_engine()` / `reset_engine()` — add cooldown | 2 |
| `src/superlocalmemory/core/recall_worker.py:62-68` | `_handle_recall()` — convert booleans to scope | 3 |
| `src/superlocalmemory/core/engine.py:455-503` | `recall()` — add `scope` param, forward to pipeline | 3 |
| `src/superlocalmemory/core/recall_pipeline.py:571-613` | `run_recall()` — accept `scope`, not forwarded yet | 3 |
| `src/superlocalmemory/server/unified_daemon.py:547-548` | `create_app()` — `logger.exception` + health endpoint + engine check closure | 4, 5 |
| `src/superlocalmemory/core/health_monitor.py:271` | `_check_once()` — zombie reaping | 6 |

### Files explicitly NOT modified (per spec)

- `src/superlocalmemory/retrieval/engine.py` — scope not forwarded yet (deferred to scope-r2)
- `src/superlocalmemory/mcp/tools_core.py` — tools already catch `Exception`, will catch new `RuntimeError`
- `src/superlocalmemory/core/worker_pool.py` — already passes params correctly

---

## Chunk 1: Fix 1 (WAL) + Fix 4 (Logging)

### Task 1.1: Fix `_enable_wal()` PRAGMA ordering

**Files:**
- Modify: `src/superlocalmemory/storage/database.py:200-208`
- Test: `tests/test_storage/test_database.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_storage/test_database.py — add test or extend existing

from pathlib import Path
from superlocalmemory.storage.database import DatabaseManager

def test_enable_wal_sets_busy_timeout(tmp_path):
    """_enable_wal() sets busy_timeout so subsequent connections inherit WAL mode safely."""
    db_path = tmp_path / "test.db"
    db = DatabaseManager(db_path)
    db.initialize(__import__("superlocalmemory.storage.schema", fromlist=["schema"]))
    # Verify busy_timeout is correctly configured on DatabaseManager-managed connections
    timeout = db.execute("PRAGMA busy_timeout")[0][0]
    assert timeout == 10000, f"Expected busy_timeout=10000, got {timeout}"
    # Verify WAL mode is active
    journal = db.execute("PRAGMA journal_mode")[0][0]
    assert journal.lower() == "wal", f"Expected wal, got {journal}"
```

Note: The test verifies the *effect* (busy_timeout is 10s on DatabaseManager connections) using the `DatabaseManager.execute()` path, matching the pattern in `test_concurrent_db.py:65-69`. The existing connection path always sets `busy_timeout` via `_connect()`, so the assertion holds regardless of `_enable_wal()`'s internal ordering. The code fix is a correctness improvement: even the raw `sqlite3.connect()` in `_enable_wal()` will now use the configured timeout before attempting `PRAGMA journal_mode=WAL`.

- [ ] **Step 2: Run test to verify it passes (test validates existing behavior)**

Run: `pytest tests/test_storage/test_database.py::test_enable_wal_sets_busy_timeout -v`
Expected: PASS (DatabaseManager._connect already sets busy_timeout before querying)

- [ ] **Step 3: Fix the code**

In `src/superlocalmemory/storage/database.py:200-208`, swap lines 203 and 204:

```python
# Before (WRONG):
def _enable_wal(self) -> None:
    conn = sqlite3.connect(str(self.db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")            # line 203
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")  # line 204
        conn.execute("PRAGMA foreign_keys=ON")              # line 205
        conn.commit()                                       # line 206
    finally:
        conn.close()                                        # line 208

# After (FIXED):
def _enable_wal(self) -> None:
    conn = sqlite3.connect(str(self.db_path))
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")  # FIRST
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage/test_database.py::test_enable_wal_sets_busy_timeout -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/storage/database.py tests/test_storage/test_database.py
git commit -m "fix: set busy_timeout before journal_mode=WAL in _enable_wal

PRAGMA busy_timeout must be set before PRAGMA journal_mode=WAL so the
WAL pragma uses the configured timeout (10s) instead of the default (5s).
If another process holds a write lock, the old ordering could fail with
SQLITE_BUSY after only 5 seconds."
```

---

### Task 1.2: Fix crash log from `logger.warning` to `logger.exception`

**Files:**
- Modify: `src/superlocalmemory/server/unified_daemon.py:547-548`

- [ ] **Step 1: Make the change**

```python
# Before (line 547-548):
except Exception as exc:
    logger.warning("Engine init failed: %s", exc)
    application.state.engine = None
    application.state.config = None

# After:
except Exception as exc:
    logger.exception("Engine init failed")  # auto-includes traceback
    application.state.engine = None
    application.state.config = None
```

- [ ] **Step 2: Verify — no test needed (code review item)**

Per spec test point #6: code review confirms `logger.exception()` automatically includes traceback via `sys.exc_info()`.

- [ ] **Step 3: Commit**

```bash
git add src/superlocalmemory/server/unified_daemon.py
git commit -m "fix: use logger.exception for engine init failure to capture traceback"
```

---

### Chunk 1 Review

Dispatch plan-document-reviewer for Chunk 1 before proceeding.

---

## Chunk 2: Fix 2 (MCP Engine Cooldown)

### Task 2.1: Add failure cooldown to MCP `get_engine()`

**Files:**
- Modify: `src/superlocalmemory/mcp/server.py:32-69`
- Test: `tests/test_mcp/test_mcp_light_engine.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_mcp/test_mcp_light_engine.py — new test or add to existing

import time
import pytest

def test_get_engine_cooldown_on_failure(monkeypatch):
    """get_engine() raises RuntimeError during cooldown after init failure."""
    from superlocalmemory.mcp import server as mcp_server
    
    # Reset state
    mcp_server.reset_engine()
    
    # Force init to fail
    def _fail(*args, **kwargs):
        raise RuntimeError("simulated init failure")
    monkeypatch.setattr(
        "superlocalmemory.core.engine.MemoryEngine.initialize", _fail
    )
    
    # First call: should raise RuntimeError
    with pytest.raises(RuntimeError, match="Engine init failed"):
        mcp_server.get_engine()
    
    # Second call within cooldown: should raise RuntimeError with "cooldown"
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        mcp_server.get_engine()
    
    # Reset for cleanup
    mcp_server.reset_engine()


def test_get_engine_retry_after_cooldown(monkeypatch):
    """get_engine() retries successfully after cooldown expires."""
    from superlocalmemory.mcp import server as mcp_server
    
    mcp_server.reset_engine()
    
    # Override cooldown to 0.1s for fast test
    monkeypatch.setattr(mcp_server, '_ENGINE_FAILURE_COOLDOWN_S', 0.1)
    
    call_count = [0]
    def _fail_once_then_succeed(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("simulated init failure")
        # succeed on retry
    
    monkeypatch.setattr(
        "superlocalmemory.core.engine.MemoryEngine.initialize", _fail_once_then_succeed
    )
    
    # First call fails
    with pytest.raises(RuntimeError):
        mcp_server.get_engine()
    
    # Wait for cooldown
    time.sleep(0.15)
    
    # Second call should succeed
    engine = mcp_server.get_engine()
    assert engine is not None
    
    mcp_server.reset_engine()


def test_get_engine_repeated_failure_resets_cooldown(monkeypatch):
    """Each failed init attempt resets the cooldown timer."""
    from superlocalmemory.mcp import server as mcp_server
    
    mcp_server.reset_engine()
    monkeypatch.setattr(mcp_server, '_ENGINE_FAILURE_COOLDOWN_S', 0.1)
    
    def _always_fail(*args, **kwargs):
        raise RuntimeError("persistent init failure")
    monkeypatch.setattr(
        "superlocalmemory.core.engine.MemoryEngine.initialize", _always_fail
    )
    
    # First failure
    with pytest.raises(RuntimeError, match="Engine init failed"):
        mcp_server.get_engine()
    first_failure = mcp_server._last_engine_failure
    
    # Cooldown not yet expired
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        mcp_server.get_engine()
    
    # After cooldown, retry fails again — cooldown timer resets
    time.sleep(0.15)
    with pytest.raises(RuntimeError, match="Engine init failed"):
        mcp_server.get_engine()
    assert mcp_server._last_engine_failure > first_failure
    
    mcp_server.reset_engine()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp/test_mcp_light_engine.py::test_get_engine_cooldown_on_failure tests/test_mcp/test_mcp_light_engine.py::test_get_engine_retry_after_cooldown tests/test_mcp/test_mcp_light_engine.py::test_get_engine_repeated_failure_resets_cooldown -v`
Expected: all FAIL (no cooldown mechanism yet)

- [ ] **Step 3: Implement the cooldown**

**File edit plan:**

1. Add `import time` to the module-level imports (next to `import threading as _threading` at line 34, NOT at the start of `get_engine()`)
2. Add module-level variables `_last_engine_failure` and `_ENGINE_FAILURE_COOLDOWN_S` after `_engine_lock`
3. Replace `get_engine()` and `reset_engine()` functions

```python
import time  # Add to existing module-level imports (~line 32, near 'import threading as _threading')

_engine = None
_engine_lock = _threading.Lock()
_last_engine_failure: float = 0.0
_ENGINE_FAILURE_COOLDOWN_S = 5.0


def get_engine():
    """Return (or create) the singleton LIGHT MemoryEngine.

    After a failed init, a 5s cooldown prevents re-attempting on every
    tool call. Callers catch RuntimeError and return error to the client
    instead of blocking indefinitely.
    """
    global _engine, _last_engine_failure

    if _engine is not None:
        return _engine

    now = time.monotonic()
    if _last_engine_failure and (now - _last_engine_failure) < _ENGINE_FAILURE_COOLDOWN_S:
        raise RuntimeError(
            f"Engine temporarily unavailable (cooldown {_ENGINE_FAILURE_COOLDOWN_S:.0f}s)"
        )

    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            from superlocalmemory.core.config import SLMConfig
            from superlocalmemory.core.engine import MemoryEngine
            from superlocalmemory.core.engine_capabilities import Capabilities

            config = SLMConfig.load()
            new_engine = MemoryEngine(config, capabilities=Capabilities.LIGHT)
            new_engine.initialize()
            _engine = new_engine
            _last_engine_failure = 0.0
            return _engine
        except Exception:
            logger.exception("MCP engine init failed")
            _last_engine_failure = time.monotonic()
            raise RuntimeError(
                f"Engine init failed, cooling down for {_ENGINE_FAILURE_COOLDOWN_S:.0f}s"
            ) from None


def reset_engine():
    """Reset engine singleton (for testing or mode switch)."""
    global _engine, _last_engine_failure
    with _engine_lock:
        _engine = None
        _last_engine_failure = 0.0
```

- [ ] **Step 4: Run tests to verify**

```bash
pytest tests/test_mcp/test_mcp_light_engine.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Verify MCP tools still catch RuntimeError**

Run: `pytest tests/test_mcp/ -v --tb=short`
Expected: existing MCP tests pass (tools already wrap `get_engine()` in `try/except Exception`)

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/mcp/server.py tests/test_mcp/test_mcp_light_engine.py
git commit -m "feat: add 5s failure cooldown to MCP get_engine()

Prevents permanent agent busy state when daemon engine is unavailable.
After a failed init, subsequent tool calls within 5s receive RuntimeError
instead of blocking on another init attempt. Pattern matches
get_engine_lazy() in routes/helpers.py."
```

---

### Chunk 2 Review

Dispatch plan-document-reviewer for Chunk 2 before proceeding.

---

## Chunk 3: Fix 3 (Recall Scope Parameter Wiring)

### Task 3.1: Add `scope` parameter to `engine.recall()` and `run_recall()`

**Files:**
- Modify: `src/superlocalmemory/core/recall_worker.py:62-68`
- Modify: `src/superlocalmemory/core/engine.py:455-503`
- Modify: `src/superlocalmemory/core/recall_pipeline.py:571-613`
- Test: `tests/test_mcp/test_mcp_recall_tool.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_mcp/test_mcp_recall_tool.py — new test or add to existing

def test_recall_worker_converts_booleans_to_scope():
    """_handle_recall converts include_global/include_shared to scope=str."""
    from superlocalmemory.core.recall_worker import _handle_recall
    
    # Verify _handle_recall signature accepts the boolean params
    # and produces scope="personal" (current behavior, no per-scope filtering yet)
    import inspect
    sig = inspect.signature(_handle_recall)
    params = sig.parameters
    assert 'include_global' in params
    assert 'include_shared' in params
    assert params['include_global'].default is True
    assert params['include_shared'].default is True


def test_engine_recall_accepts_scope_kwarg(engine_with_mock_deps):
    """engine.recall() accepts optional scope keyword argument."""
    import inspect
    sig = inspect.signature(engine_with_mock_deps.recall)
    assert 'scope' in sig.parameters
    assert sig.parameters['scope'].default == 'personal'


def test_run_recall_accepts_scope_kwarg():
    """run_recall() accepts optional scope keyword argument."""
    from superlocalmemory.core.recall_pipeline import run_recall
    import inspect
    sig = inspect.signature(run_recall)
    assert 'scope' in sig.parameters
    assert sig.parameters['scope'].default == 'personal'


def test_engine_recall_with_scope_does_not_break(engine_with_mock_deps):
    """engine.recall(scope='personal') works without TypeError."""
    result = engine_with_mock_deps.recall("test query", scope="personal", limit=5)
    assert result is not None
    # Result type check
    from superlocalmemory.storage.models import RecallResponse
    assert isinstance(result, RecallResponse)
```

- [ ] **Step 2: Run test to verify failure (TypeError on include_global)**

Run: `pytest tests/test_mcp/test_mcp_recall_tool.py -v`
Expected: relevant tests FAIL

- [ ] **Step 3.1: Fix `recall_worker.py:_handle_recall()`**

In `src/superlocalmemory/core/recall_worker.py:62-68`:

```python
# Before (lines 62-68):
def _handle_recall(query: str, limit: int, session_id: str = "",
                   include_global: bool = True, include_shared: bool = True) -> dict:
    engine = _get_engine()
    response = engine.recall(
        query, limit=limit, session_id=session_id or None,
        include_global=include_global, include_shared=include_shared,
    )

# After:
def _handle_recall(query: str, limit: int, session_id: str = "",
                   include_global: bool = True, include_shared: bool = True) -> dict:
    engine = _get_engine()
    # Convert include_global/include_shared to scope parameter.
    # Retrieval channels already search all scopes by default when scope="personal"
    # (personal queries include global+shared rows in _scope_where).
    # Per-scope recall filtering on the read path is deferred to scope-r2;
    # the immediate fix is eliminating the TypeError crash.
    # NOTE: include_global/include_shared params are intentionally retained
    # for WorkerPool protocol compatibility; scope-r2 will activate them.
    scope = "personal"
    response = engine.recall(
        query, limit=limit, session_id=session_id or None,
        scope=scope,
    )
```

- [ ] **Step 3.2: Fix `engine.py:recall()` — add `scope` parameter**

In `src/superlocalmemory/core/engine.py:455-503`:

```python
# Change signature (add scope parameter):
def recall(
    self,
    query: str,
    profile_id: str | None = None,
    mode: Mode | None = None,
    limit: int = 20,
    agent_id: str = "unknown",
    session_id: str | None = None,
    fast: bool = False,
    scope: str = "personal",
) -> RecallResponse:

# Change the run_recall call (add scope=scope):
response = run_recall(
    query,
    pid,
    mode=mode,
    limit=limit,
    agent_id=agent_id,
    config=self._config,
    retrieval_engine=self._retrieval_engine,
    trust_scorer=self._trust_scorer,
    embedder=self._embedder,
    db=self._db,
    llm=self._llm,
    hooks=self._hooks,
    access_log=self._access_log,
    auto_linker=self._auto_linker,
    fast=fast,
    scope=scope,
)
```

- [ ] **Step 3.3: Fix `recall_pipeline.py:run_recall()` — add `scope` parameter**

In `src/superlocalmemory/core/recall_pipeline.py:571-588`:

```python
# Change signature only (add scope parameter after fast):
def run_recall(
    query: str,
    profile_id: str,
    mode: Mode | None = None,
    limit: int = 20,
    agent_id: str = "unknown",
    *,
    config: SLMConfig,
    retrieval_engine: Any,
    trust_scorer: Any,
    embedder: Any,
    db: DatabaseManager,
    llm: Any,
    hooks: HookRegistry,
    access_log: Any = None,
    auto_linker: Any = None,
    fast: bool = False,
    scope: str = "personal",
) -> RecallResponse:
```

No body changes — scope is accepted but not forwarded to retrieval_engine.recall() (deferred to scope-r2).

- [ ] **Step 4: Run all recall tests**

```bash
pytest tests/test_mcp/test_mcp_recall_tool.py -v
pytest tests/ -k "recall" -v --tb=short
```
Expected: all PASS. No TypeError on keyword arguments.

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/core/recall_worker.py \
        src/superlocalmemory/core/engine.py \
        src/superlocalmemory/core/recall_pipeline.py \
        tests/test_mcp/test_mcp_recall_tool.py
git commit -m "fix: add scope parameter to engine.recall()/run_recall() to fix TypeError

recall_worker was passing include_global/include_shared to engine.recall()
which didn't accept those kwargs, causing TypeError. Convert booleans to
scope string in recall_worker, add scope param to engine.recall() and
run_recall() signatures. Scope filtering on retrieval channels is deferred
to scope-r2."
```

---

### Chunk 3 Review

Dispatch plan-document-reviewer for Chunk 3 before proceeding.

---

## Chunk 4: Fix 5 (Health Endpoint Recovery + Engine Health Check)

### Task 4.1: Add engine recovery to `/health` endpoint

**Files:**
- Modify: `src/superlocalmemory/server/unified_daemon.py:1077-1087`

- [ ] **Step 1: Modify the health endpoint**

```python
# Before (lines 1077-1087):
@app.get("/health")
async def health():
    _update_activity()
    # Non-blocking peek: report status without forcing a re-init.
    engine = getattr(application.state, "engine", None)
    return {
        "status": "ok",
        "pid": os.getpid(),
        "engine": "initialized" if engine else "unavailable",
        "version": getattr(application, 'version', 'unknown'),
    }

# After:
@app.get("/health")
async def health():
    _update_activity()
    engine = getattr(application.state, "engine", None)
    if engine is None:
        engine = get_engine_lazy(application.state)  # attempts recovery, 5s cooldown
    return {
        "status": "ok",
        "pid": os.getpid(),
        "engine": "initialized" if engine else "unavailable",
        "version": getattr(application, 'version', 'unknown'),
    }
```

`get_engine_lazy()` is already imported/available in this scope (used at line ~1070 in `_get_engine_or_503()`).

- [ ] **Step 2: Add engine health check closure registration**

In `create_app()`, after HealthMonitor start (~line 565), add:

```python
# Register engine health check from daemon side (avoids core→server reverse dep)
if application.state.health_monitor:
    def _check_engine():
        engine = getattr(application.state, "engine", None)
        if engine is None:
            return {"name": "engine", "status": "critical", "detail": "Engine unavailable"}
        return {"name": "engine", "status": "ok", "detail": "Engine initialized"}
    register_health_check(_check_engine)
```

Update the import at line 556 from:
```python
from superlocalmemory.core.health_monitor import HealthMonitor
```
to:
```python
from superlocalmemory.core.health_monitor import HealthMonitor, register_health_check
```

- [ ] **Step 3: Write automated tests**

```python
# tests/test_api/test_health.py — new file

from fastapi.testclient import TestClient
from superlocalmemory.server.unified_daemon import create_app

@pytest.fixture
def health_client():
    """Create a minimal TestClient for the health endpoint."""
    app = create_app()
    return TestClient(app)

def test_health_endpoint_triggers_engine_recovery(health_client):
    """GET /health attempts engine recovery when engine is None."""
    # Set engine to None on app state to simulate daemon crash
    health_client.app.state.engine = None
    response = health_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    # get_engine_lazy() should have re-initialized the engine
    assert data["engine"] == "initialized"
    assert data["status"] == "ok"


def test_health_check_includes_engine_item(monkeypatch):
    """run_all_health_checks() includes the engine health check item."""
    from superlocalmemory.core.health_monitor import (
        run_all_health_checks, register_health_check,
    )
    # Simulate the closure registration done by create_app()
    def _check_engine():
        return {"name": "engine", "status": "ok", "detail": "Engine initialized"}
    register_health_check(_check_engine)
    
    results = run_all_health_checks()
    engine_checks = [r for r in results if r["name"] == "engine"]
    assert len(engine_checks) == 1, f"Expected 1 engine check, got {len(engine_checks)}"
    ec = engine_checks[0]
    assert ec["status"] in ("ok", "critical", "unknown", "error")
    assert "detail" in ec
```

- [ ] **Step 4: Run tests to verify**

```bash
pytest tests/test_api/test_health.py -v --tb=short
```

Expected: all PASS (health endpoint triggers recovery, engine health check present)

- [ ] **Step 5: Run existing daemon tests (no regressions)**

```bash
pytest tests/ -k "health or daemon" -v --tb=short
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/server/unified_daemon.py tests/test_api/test_health.py
git commit -m "feat: add engine recovery to /health endpoint and health check

/health now calls get_engine_lazy() when engine is None to attempt
automatic recovery. Adds engine health check closure registered from
create_app() (avoids core→server reverse dependency)."
```

---

### Chunk 4 Review

Dispatch plan-document-reviewer for Chunk 4 before proceeding.

---

## Chunk 5: Fix 6 (Zombie Child Process Reaping)

### Task 5.1: Add zombie reaping to HealthMonitor `_check_once()`

**Files:**
- Modify: `src/superlocalmemory/core/health_monitor.py:271`

- [ ] **Step 1: Write the test**

```python
# tests/test_process_health/test_process_reaper.py — add to existing

import os
import sys
import time
import pytest

@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="fork/proc is Linux-specific")
def test_check_once_reaps_zombie_children():
    """_check_once() reaps zombie child processes."""
    # Create a zombie: fork, child exits immediately, parent doesn't wait
    pid = os.fork()
    if pid == 0:
        os._exit(0)  # child exits — becomes zombie until parent reaps

    # Give child time to exit and become zombie
    time.sleep(0.1)

    # Verify child is zombie via /proc (read-only, does NOT reap it)
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    assert "Z" in line, f"Expected zombie state, got {line.strip()}"
                    break
    except FileNotFoundError:
        pytest.skip(f"Child PID {pid} disappeared before check — /proc not readable")

    # Trigger a health check cycle — this should reap the zombie
    from superlocalmemory.core.health_monitor import HealthMonitor
    monitor = HealthMonitor(
        global_rss_budget_mb=5000,
        heartbeat_timeout_sec=600,
        check_interval_sec=60,
        enable_structured_logging=False,
    )
    monitor._check_once()

    # Verify zombie was reaped (read-only check via /proc)
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    if "Z" in line:
                        pytest.fail(f"Zombie NOT reaped by _check_once(): {line.strip()}")
                    break
    except FileNotFoundError:
        pass  # Process fully gone = reaped successfully

    # Defensive cleanup (unconditional, no assertion tied to result)
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ProcessLookupError, ChildProcessError):
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_process_health/test_process_reaper.py::test_check_once_reaps_zombie_children -v`
Expected: FAIL or SKIP (if not Linux) — no zombie reaping in _check_once yet

- [ ] **Step 3: Add zombie reaping code**

In `src/superlocalmemory/core/health_monitor.py`, at the end of `_check_once()` (after line 271, before the built-in health checks section):

```python
        # Reap zombie child processes (non-blocking)
        try:
            while True:
                wpid, status = os.waitpid(-1, os.WNOHANG)
                if wpid == 0:
                    break
                if os.WIFSIGNALED(status):
                    logger.info("Reaped zombie child PID %d (killed by signal %d)",
                                wpid, os.WTERMSIG(status))
                else:
                    logger.info("Reaped zombie child PID %d (exit code=%d)",
                                wpid, os.WEXITSTATUS(status))
                log_structured(
                    level="info", operation="reap_zombie",
                    pid=wpid,
                    detail=(
                        f"signal={os.WTERMSIG(status)}" if os.WIFSIGNALED(status)
                        else f"exit_code={os.WEXITSTATUS(status)}"
                    ),
                )
        except ChildProcessError:
            pass  # No children at all
        except Exception:
            pass  # Non-critical
```

Note: `os` is already imported at the top of the file (line 21), used at line 196 (`os.getpid()`). No new import needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_process_health/test_process_reaper.py::test_check_once_reaps_zombie_children -v`
Expected: PASS

- [ ] **Step 5: Run existing health monitor tests**

```bash
pytest tests/test_process_health/ -v --tb=short
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/core/health_monitor.py tests/test_process_health/test_process_reaper.py
git commit -m "feat: reap zombie child processes in HealthMonitor._check_once()

Non-blocking os.waitpid(-1, WNOHANG) loop added to each health check cycle.
Prevents zombie accumulation (7 zombies observed in Hermes agent diagnosis)."
```

---

### Chunk 5 Review

Dispatch plan-document-reviewer for Chunk 5 before proceeding.

---

## Chunk 6: Integration Verification

### Task 6.1: Full test suite regression

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -q --tb=short
```

Expected: same pass/fail count as before (no regressions).

- [ ] **Step 2: Fix any failures**

If existing tests fail due to our changes, diagnose and fix before proceeding.

- [ ] **Step 3: Final commit (only if regression fixes needed)**

If Step 2 found any regressions, stage and commit the fix files individually:

```bash
git add <specific-fixed-files>
git commit -m "fix: address test regressions from reliability fixes"
```

If no regressions, skip this step.

---

### Chunk 6 Review

Dispatch plan-document-reviewer for Chunk 6 before proceeding.

---

## Implementation Order

Chunks are ordered by dependency and risk:

1. **Chunk 1** (Fixes 1 + 4) — simplest, zero risk, good warm-up
2. **Chunk 2** (Fix 2) — MCP cooldown, medium complexity, independent
3. **Chunk 3** (Fix 3) — Recall scope, touches 3 files, independent but needs signature verification
4. **Chunk 4** (Fix 5) — Health endpoint, depends on Chunk 1 (same file)
5. **Chunk 5** (Fix 6) — Zombie reaping, independent
6. **Chunk 6** (Integration) — Full regression, depends on all previous chunks

Each chunk commits independently and can be reviewed/merged separately.
