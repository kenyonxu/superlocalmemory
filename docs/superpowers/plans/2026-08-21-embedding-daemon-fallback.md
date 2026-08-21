# EmbeddingService daemon fallback 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让嵌入式 FULL engine 宿主在嵌入 worker 单例被他进程持有(或内存压力)且 daemon 在线时,经 daemon `/api/v3/embed` 完成嵌入,恢复 recall 全通道;daemon 不可达时行为与现状完全一致。

**Architecture:** 扩展 `McpEmbedderProxy`(strict/timeout 参数)复用 v3.5.9 端点;`EmbeddingService` 新增 `_daemon_fallback` 属性与 attach/detach/失败计数逻辑;`_subprocess_embed()` 的 `_available is False` 短路处优先委托 proxy。engine.py、daemon 端点零改动。

**Tech Stack:** Python 3.13、httpx、pytest、`/home/kai-remote/miniconda3/bin/python`(仓库无 .venv;所有 pytest 调用用 `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest`)。

**Spec:** `docs/superpowers/specs/2026-08-21-embedding-daemon-fallback-design.md`(四个已批准决策:无凭据 loopback / 双触发条件 / 对外报可用 / 默认开启+`SLM_EMBED_DAEMON_FALLBACK=0` 可关)

## Global Constraints

- `McpEmbedderProxy` 构造默认值**不得变化**:`timeout=5.0`、`strict=False`——LIGHT 路径(`engine.py:_try_init_proxy` 调 `McpEmbedderProxy(port=port)`)行为零变化。
- `_available` 三态语义神圣不可破:`None` 是 recall_health 的重探测信号,必须 fall through 重试 spawn,**不得**走 proxy 短路(embeddings.py:462-466 历史事故注释)。
- 失败归因:只有"worker 被他进程持有(acquire 失败 / PID 存活)"与"内存压力"三类分支触发 attach;spawn 崩溃、通信超时等真故障不降级。
- 优先级单调收敛:本地 worker > daemon proxy > None。
- 不修改 daemon 任何端点;不修改 `McpEmbedderProxy` 的现有消费方行为;不动 `compute_fisher_params`、reranker。
- 端口来源:`int(os.environ.get("SLM_DAEMON_PORT", "") or 8765)`(与 `cli/daemon.py:52` 一致)。
- 超时来源:`float(os.environ.get("SLM_EMBED_DAEMON_TIMEOUT", "30"))`。
- AGENTS.md 强制:提交前运行 gitnexus `detect_changes`(无 MCP 环境时退化为人工核对 `git diff --stat` 范围)。
- 执行前先建隔离 worktree(`git worktree add /tmp/slm-planb main`,分支 `feat/embedding-daemon-fallback`),全部工作在 worktree 内进行。

---

### Task 1: McpEmbedderProxy strict/timeout 扩展

**Files:**
- Modify: `src/superlocalmemory/core/mcp_embedder_proxy.py`(全文 90 行)
- Test: `tests/test_core/test_mcp_embedder_proxy.py`(新建)

**Interfaces:**
- Consumes: 现有 `McpEmbedderProxy(port=8765, timeout=5.0)`,daemon 端点 `GET /api/v3/embed/ping`、`POST /api/v3/embed`
- Produces: `McpEmbedderProxy(port: int = 8765, timeout: float = 5.0, strict: bool = False)`;strict 模式下 `embed()`/`embed_batch()` 失败时**重抛原始异常**(httpx 异常或 ValueError);非 strict 行为与现状完全一致。Task 2 以 `McpEmbedderProxy(port, timeout=30.0, strict=True)` 消费。

- [ ] **Step 1: 写失败测试(新文件)**

```python
# tests/test_core/test_mcp_embedder_proxy.py
"""Tests for McpEmbedderProxy strict mode and timeout configurability."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from superlocalmemory.core.mcp_embedder_proxy import McpEmbedderProxy


class TestDefaultsUnchanged:
    def test_default_timeout_and_non_strict(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        assert proxy._timeout == 5.0
        assert proxy._strict is False

    def test_non_strict_returns_nones_on_error(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            assert proxy.embed_batch(["a", "b"]) == [None, None]

    def test_negative_ping_not_cached(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            assert proxy.is_available() is False
        ok = MagicMock(); ok.status_code = 200
        with patch("httpx.get", return_value=ok):
            assert proxy.is_available() is True  # retried, not sticky-False


class TestStrictMode:
    def test_strict_reraises_connect_error(self) -> None:
        proxy = McpEmbedderProxy(port=9999, strict=True)
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(httpx.ConnectError):
                proxy.embed_batch(["a"])

    def test_strict_reraises_read_timeout(self) -> None:
        proxy = McpEmbedderProxy(port=9999, strict=True)
        with patch("httpx.post", side_effect=httpx.ReadTimeout("slow")):
            with pytest.raises(httpx.ReadTimeout):
                proxy.embed_batch(["a"])

    def test_strict_passes_through_success(self) -> None:
        proxy = McpEmbedderProxy(port=9999, timeout=30.0, strict=True)
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1, 0.2]]}
        resp.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=resp) as post:
            assert proxy.embed_batch(["x"]) == [[0.1, 0.2]]
        assert post.call_args.kwargs["timeout"] == 30.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_core/test_mcp_embedder_proxy.py -q`
Expected: FAIL(`_strict` 属性不存在 / strict 未重抛)

- [ ] **Step 3: 实现(对 mcp_embedder_proxy.py 的三处修改)**

构造函数加 `strict: bool = False`,存 `self._strict = strict`;docstring 更新(strict 用途一句话)。`embed_batch()` 的 `except Exception as exc:` 块开头加:

```python
        except Exception as exc:
            if self._strict:
                raise
            logger.debug("McpEmbedderProxy.embed_batch failed: %s", exc)
            return [None] * len(texts)
```

注意:`embed()` 经 `embed_batch()` 传播,无需单独改。`is_available()` 现状已是"否定不缓存"(仅 `True` 短路),不改。

- [ ] **Step 4: 跑测试确认通过 + LIGHT 回归**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_core/test_mcp_embedder_proxy.py tests/test_mcp/test_mcp_light_engine.py tests/test_core/test_engine_capabilities.py -q`
Expected: 全 PASS(后两个文件是 v3.5.9 proxy 的既有消费方测试,验证零行为变化)

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/core/mcp_embedder_proxy.py tests/test_core/test_mcp_embedder_proxy.py
git commit -m "feat(embeddings): strict+timeout options on McpEmbedderProxy (daemon fallback prep)"
```

---

### Task 2: EmbeddingService fallback 核心(attach/detach/委托/失败计数)

**Files:**
- Modify: `src/superlocalmemory/core/embeddings.py`(`__init__` ~213-234、`_ensure_worker` ~700-731、`_subprocess_embed` ~455-470)
- Test: `tests/test_core/test_embedding_daemon_fallback.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `McpEmbedderProxy(port, timeout, strict=True)`;embeddings.py 现有 `acquire_embedding_lock()`、`_is_embedding_worker_alive()`、`_check_memory_pressure()`、`_request_lock()`
- Produces(供 Task 3 与测试消费):
  - `svc._daemon_fallback: McpEmbedderProxy | None`
  - `svc._fallback_fail_count: int`、`svc._fallback_served: int`、`svc._fallback_read_timeouts: int`
  - `svc._try_attach_daemon_fallback() -> None`
  - `svc._embed_via_daemon(texts: list[str]) -> list[list[float] | None] | None`
  - `svc._record_fallback_failure(exc: Exception) -> None`
  - 环境变量:`SLM_EMBED_DAEMON_FALLBACK`("0" 关闭,默认开)、`SLM_EMBED_DAEMON_TIMEOUT`(默认 "30")、`SLM_DAEMON_PORT`(默认 8765)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_core/test_embedding_daemon_fallback.py
"""EmbeddingService daemon fallback: singleton held / memory pressure."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.embeddings import EmbeddingService


def _svc() -> EmbeddingService:
    return EmbeddingService(EmbeddingConfig(dimension=384))


class TestAttach:
    def test_attach_on_singleton_held_and_daemon_online(self) -> None:
        svc = _svc()
        proxy = MagicMock()
        proxy.is_available.return_value = True
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=False), \
             patch("superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy", return_value=proxy):
            svc._ensure_worker()
        assert svc._available is False           # 内部事实不变
        assert svc._daemon_fallback is proxy

    def test_no_attach_when_daemon_offline(self) -> None:
        svc = _svc()
        proxy = MagicMock()
        proxy.is_available.return_value = False
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=False), \
             patch("superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy", return_value=proxy):
            svc._ensure_worker()
        assert svc._available is False
        assert svc._daemon_fallback is None

    def test_attach_on_memory_pressure(self) -> None:
        svc = _svc()
        proxy = MagicMock()
        proxy.is_available.return_value = True
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=True), \
             patch("superlocalmemory.core.embeddings._is_embedding_worker_alive", return_value=False), \
             patch.object(EmbeddingService, "_check_memory_pressure", return_value=False), \
             patch("superlocalmemory.core.embeddings.release_embedding_lock"), \
             patch("superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy", return_value=proxy):
            svc._ensure_worker()
        assert svc._daemon_fallback is proxy

    def test_env_opt_out(self, monkeypatch) -> None:
        monkeypatch.setenv("SLM_EMBED_DAEMON_FALLBACK", "0")
        svc = _svc()
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=False):
            svc._ensure_worker()
        assert svc._available is False
        assert svc._daemon_fallback is None


class TestDelegation:
    def test_embed_delegates_to_proxy_when_disabled(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.return_value = [[0.1] * 384]
        svc._daemon_fallback = proxy
        assert svc.embed("hello") == [0.1] * 384
        assert svc._fallback_served == 1

    def test_none_when_no_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        assert svc.embed("hello") is None  # 现状不劣化

    def test_none_availability_never_short_circuits_to_proxy(self) -> None:
        # 三态防线:None 是 heal 重探测信号,必须 fall through 到 _ensure_worker
        svc = _svc()
        svc._available = None
        svc._daemon_fallback = MagicMock()
        with patch.object(EmbeddingService, "_ensure_worker") as ensure, \
             patch.object(EmbeddingService, "_send_request", return_value={"ok": True, "embeddings": [[0.1] * 384]}):
            svc._worker_proc = MagicMock()
            svc._worker_proc.poll.return_value = None
            svc.embed("hello")
        ensure.assert_called_once()
        svc._daemon_fallback.embed_batch.assert_not_called()


class TestFailureCounting:
    def test_detach_after_three_connect_errors(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.side_effect = httpx.ConnectError("refused")
        svc._daemon_fallback = proxy
        for _ in range(3):
            assert svc.embed("x") is None
        assert svc._daemon_fallback is None  # detached → 回到现状

    def test_read_timeout_counts_only_after_two_consecutive(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        svc._daemon_fallback = proxy
        proxy.embed_batch.side_effect = httpx.ReadTimeout("slow")
        svc.embed("x")
        assert svc._fallback_fail_count == 0   # 首次宽容
        svc.embed("x")
        assert svc._fallback_fail_count == 1   # 连续第二次计 1
        proxy.embed_batch.side_effect = [[0.1] * 384]  # 成功重置
        svc.embed("x")
        assert svc._fallback_read_timeouts == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_core/test_embedding_daemon_fallback.py -q`
Expected: FAIL(`_daemon_fallback` 属性不存在等)

- [ ] **Step 3: 实现 — `__init__` 加四个属性**

```python
        self._daemon_fallback: object | None = None
        self._fallback_fail_count: int = 0
        self._fallback_served: int = 0
        self._fallback_read_timeouts: int = 0
```

- [ ] **Step 4: 实现 — 三个新方法(放 `_ensure_worker` 之前的合适位置)**

```python
    def _try_attach_daemon_fallback(self) -> None:
        """Attach a daemon embed proxy when the local worker cannot spawn.

        Only called from _ensure_worker's give-up branches (singleton held by
        another process, memory pressure). Never masks real spawn failures.
        """
        if os.environ.get("SLM_EMBED_DAEMON_FALLBACK", "1") == "0":
            return
        if self._daemon_fallback is not None:
            return
        port = int(os.environ.get("SLM_DAEMON_PORT", "") or 8765)
        timeout = float(os.environ.get("SLM_EMBED_DAEMON_TIMEOUT", "30"))
        try:
            from superlocalmemory.core.mcp_embedder_proxy import McpEmbedderProxy
            proxy = McpEmbedderProxy(port=port, timeout=timeout, strict=True)
            if proxy.is_available():
                self._daemon_fallback = proxy
                self._fallback_fail_count = 0
                logger.info(
                    "Embedding worker unavailable locally — "
                    "using daemon fallback (port %d)", port,
                )
        except Exception as exc:
            logger.debug("Daemon fallback attach skipped: %s", exc)

    def _embed_via_daemon(self, texts: list[str]) -> list[list[float] | None] | None:
        """Delegate to the daemon fallback with failure accounting."""
        assert self._daemon_fallback is not None
        try:
            result = self._daemon_fallback.embed_batch(texts)
        except Exception as exc:  # strict proxy re-raises httpx/ValueError
            self._record_fallback_failure(exc)
            return None
        self._fallback_served += 1
        self._fallback_read_timeouts = 0
        return result

    def _record_fallback_failure(self, exc: Exception) -> None:
        """Classify a proxy failure; detach after the threshold (3)."""
        import httpx
        if isinstance(exc, httpx.ReadTimeout):
            # Cold-start tolerance: only every second CONSECUTIVE read
            # timeout counts as one failure.
            self._fallback_read_timeouts += 1
            if self._fallback_read_timeouts < 2:
                return
            self._fallback_read_timeouts = 0
        self._fallback_fail_count += 1
        if self._fallback_fail_count >= 3:
            logger.warning(
                "Daemon fallback detached after %d failures (last: %s)",
                self._fallback_fail_count, exc,
            )
            self._daemon_fallback = None
            self._fallback_fail_count = 0
```

- [ ] **Step 5: 实现 — `_ensure_worker` 三处接入 + `_subprocess_embed` 委托**

`_ensure_worker` 的三处 `self._available = False` 之前各加一行 `self._try_attach_daemon_fallback()`(acquire 失败分支、PID 存活分支、内存压力分支;均在 `return` 前)。

`_subprocess_embed` 的短路处改为:

```python
            if self._available is False:
                if self._daemon_fallback is not None:
                    return self._embed_via_daemon(texts)
                return None
```

- [ ] **Step 6: 跑测试确认通过**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_core/test_embedding_daemon_fallback.py -q`
Expected: 9 项全 PASS

- [ ] **Step 7: 既有嵌入测试回归**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_core/test_embedding_fallback.py tests/test_core/test_embedding_worker_spawn_lock.py tests/test_core/test_embedding_worker_backend_order.py -q`
Expected: 全 PASS(三态/锁行为零回归)

- [ ] **Step 8: 提交**

```bash
git add src/superlocalmemory/core/embeddings.py tests/test_core/test_embedding_daemon_fallback.py
git commit -m "feat(embeddings): daemon fallback when local worker unavailable (singleton/memory-pressure)"
```

---

### Task 3: 对外语义(is_available / is_warm / embedder_mode / 维度信息)

**Files:**
- Modify: `src/superlocalmemory/core/embeddings.py`(`is_available` ~251-257、`is_warm` ~259-280、`_embed_via_daemon` Task 2 新增处)
- Test: `tests/test_core/test_embedding_daemon_fallback.py`(追加)

**Interfaces:**
- Consumes: Task 2 的 `_daemon_fallback`、`_fallback_served`、`_embed_via_daemon`
- Produces: `svc.embedder_mode -> str`(`"local" | "daemon-fallback" | "unavailable"`);`is_available()` 在 fallback 激活时返回 `True`;`is_warm` 在 `_fallback_served > 0` 时返回 `True`;fallback 向量维度不匹配时抛 `DimensionMismatchError` 且消息含 `"via daemon fallback"`

- [ ] **Step 1: 写失败测试(追加到既有文件)**

```python
class TestExternalSemantics:
    def test_is_available_true_with_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        svc._daemon_fallback = MagicMock()
        assert svc.is_available is True

    def test_is_available_false_without_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        assert svc.is_available is False

    def test_is_warm_after_fallback_served(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.return_value = [[0.1] * 384]
        svc._daemon_fallback = proxy
        assert svc.is_warm is False
        svc.embed("x")
        assert svc.is_warm is True

    def test_embedder_mode(self) -> None:
        svc = _svc()
        assert svc.embedder_mode == "local"
        svc._available = False
        assert svc.embedder_mode == "unavailable"
        svc._daemon_fallback = MagicMock()
        assert svc.embedder_mode == "daemon-fallback"

    def test_dimension_mismatch_message_names_daemon_fallback(self) -> None:
        from superlocalmemory.core.embeddings import DimensionMismatchError
        svc = _svc()  # dimension=384
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.return_value = [[0.1] * 8]  # 错维度
        svc._daemon_fallback = proxy
        with pytest.raises(DimensionMismatchError, match="daemon fallback"):
            svc.embed("x")

    def test_unload_is_noop_for_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        svc._daemon_fallback = MagicMock()
        assert svc.unload() in (True, False)  # 不抛异常即通过
        assert svc._daemon_fallback is not None  # fallback 存活,不受影响
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_core/test_embedding_daemon_fallback.py::TestExternalSemantics -q`
Expected: FAIL(`embedder_mode` 不存在等)

- [ ] **Step 3: 实现 — 四处修改**

`is_available()` 最终 `return self._available` 之前加:

```python
        if self._available is False and self._daemon_fallback is not None:
            return True
```

`is_warm` 的 local 分支(`proc = getattr(self, "_worker_proc", None)` 之前)加:

```python
        if self._daemon_fallback is not None and self._fallback_served > 0:
            return True
```

新增 property(放 `is_warm` 之后):

```python
    @property
    def embedder_mode(self) -> str:
        """How embeddings are currently produced: local | daemon-fallback | unavailable."""
        if self._daemon_fallback is not None:
            return "daemon-fallback"
        if self._available is False:
            return "unavailable"
        return "local"
```

`_embed_via_daemon` 的 `return result` 前加维度校验(让错误信息指明来源):

```python
        for vec in result:
            if vec is not None:
                try:
                    self._validate_dimension(np.asarray(vec))
                except DimensionMismatchError as exc:
                    raise DimensionMismatchError(f"{exc} (via daemon fallback)") from exc
```

- [ ] **Step 4: 跑测试确认通过**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_core/test_embedding_daemon_fallback.py -q`
Expected: 15 项全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/core/embeddings.py tests/test_core/test_embedding_daemon_fallback.py
git commit -m "feat(embeddings): external semantics for daemon fallback (availability/warmth/mode/dimension errors)"
```

---

### Task 4: 双进程集成测试

**Files:**
- Test: `tests/test_integration/test_embedding_fallback_two_process.py`(新建;若 `tests/test_integration/` 不存在则创建并加 `__init__.py`,先看 `tests/` 下现有集成测试目录名,有则复用)

**Interfaces:**
- Consumes: Task 2-3 全部;`acquire_embedding_lock()` / `release_embedding_lock()` / `_embedding_pid_file()`(embeddings.py);`SLM_DAEMON_PORT` 环境变量
- Produces: 无(终端测试任务)

- [ ] **Step 1: 写集成测试**

```python
# tests/test_integration/test_embedding_fallback_two_process.py
"""Two-process proof: FULL engine host recovers embeddings via daemon fallback.

Process A (this test) owns the machine-wide embedding-worker singleton
(flock + live PID file). A stub daemon (uvicorn) serves /api/v3/embed*.
Process B semantics: an EmbeddingService in-process that loses the
singleton race and must fall back to the daemon over real HTTP.
"""
from __future__ import annotations

import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.embeddings import (
    EmbeddingService,
    _embedding_pid_file,
    acquire_embedding_lock,
    release_embedding_lock,
)

_DIM = 384


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v3/embed/ping")
    async def ping() -> dict:
        return {"ok": True}

    @app.post("/api/v3/embed")
    async def embed(body: dict) -> dict:
        texts = body.get("texts", [])
        return {"embeddings": [[0.01 * (i + 1)] * _DIM for i, _ in enumerate(texts)]}

    return app


@pytest.fixture()
def stub_daemon():
    config = uvicorn.Config(_make_app(), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):  # wait for bind
        if server.started:
            break
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    monkey_env = pytest.MonkeyPatch.context
    yield port
    server.should_exit = True
    thread.join(timeout=5)


def test_fallback_recovers_embedding_over_real_http(monkeypatch, caplog) -> None:
    # 进程 A 角色:持有机器级单例(flock + 活 PID 文件)
    assert acquire_embedding_lock(timeout=5.0), "test must own the embedding lock"
    pid_file = _embedding_pid_file()
    original = pid_file.read_text() if pid_file.exists() else None
    pid_file.write_text(str(__import__("os").getpid()))
    try:
        with stub_daemon_port(monkeypatch) as port:
            monkeypatch.setenv("SLM_DAEMON_PORT", str(port))
            svc = EmbeddingService(EmbeddingConfig(dimension=_DIM))
            with caplog.at_level("WARNING"):
                vec = svc.embed("hello world")
            assert vec is not None and len(vec) == _DIM
            assert svc.embedder_mode == "daemon-fallback"
            assert svc.is_available is True
            assert svc.is_warm is True
            # 零"返回 None"类警告(回归 §9.1 的静默缺通道)
            assert not [r for r in caplog.records if "returning None" in r.getMessage()]
    finally:
        release_embedding_lock()
        if original is None:
            pid_file.unlink(missing_ok=True)
        else:
            pid_file.write_text(original)
```

辅助 context manager(放同文件):

```python
import contextlib

@contextlib.contextmanager
def stub_daemon_port(monkeypatch):
    config = uvicorn.Config(_make_app(), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)
```

(注:`stub_daemon` fixture 与 `stub_daemon_port` 二选一,以能稳定绑定 ephemeral port 的为准;实现者保留通过的那个,删除另一个。)

- [ ] **Step 2: 跑测试确认通过**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/test_integration/test_embedding_fallback_two_process.py -q`
Expected: PASS(真实 HTTP 往返 + 真实 flock/PID 单例)
注意:本测试持有机器级嵌入锁,**不得**与其他使用嵌入 worker 的测试并行;单独运行。

- [ ] **Step 3: 提交**

```bash
git add tests/test_integration/test_embedding_fallback_two_process.py
git commit -m "test(embeddings): two-process daemon fallback integration proof"
```

---

### Task 5: 回归门与文档收尾

**Files:**
- Modify: `docs/research-2026-08-21-embeddingservice-daemon-routing.md`(§9 待决策标记为已实施)、`CHANGELOG.md`(顶部添加条目)

**Interfaces:**
- Consumes: Tasks 1-4 全部
- Produces: 无(收尾任务)

- [ ] **Step 1: 全量回归**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest tests/ -q --deselect tests/test_core/test_enrich_new_facts_now.py::test_enrich_new_facts_now --deselect tests/test_core/test_enrich_new_facts_now.py::test_store_then_search`
(后台运行 + 定时轮询;两个 deselect 是 merge 时 controller 批准的既有环境缺陷,见 `.superpowers/sdd/2026-08-21-merge-upstream-4.0.9/progress.md`;若 node id 不匹配,先 `--collect-only` 查该文件实际 test id 再替换)
Expected: 0 failed(基准 9758 passed + 本 feature 新增测试)

- [ ] **Step 2: hermes 套件**

Run: `env -u ALL_PROXY -u all_proxy /home/kai-remote/miniconda3/bin/python -m pytest src/superlocalmemory/integrations/hermes/tests -q`
Expected: 92 passed

- [ ] **Step 3: gitnexus 变更核查(AGENTS.md 强制)**

用 gitnexus MCP 工具 `gitnexus_impact`(target `EmbeddingService._ensure_worker` 等被改符号)+ `gitnexus_detect_changes()` 核查影响面;无 MCP 环境时退化:`git diff main..HEAD --stat`,确认改动仅限 `mcp_embedder_proxy.py`、`embeddings.py`、3 个测试文件、文档。

- [ ] **Step 4: 文档收尾**

`docs/research-2026-08-21-embeddingservice-daemon-routing.md` §9 三个待决策旁标注"(2026-08-21 已定并实施:无凭据 loopback / 双触发 / 报可用,见 spec)";`CHANGELOG.md` 顶部加:

```markdown
## mslm 4.2.0+ — EmbeddingService daemon fallback (2026-08-21)
- Embedded FULL-engine hosts (gateway, any embedded consumer) now recover
  full-channel recall via the daemon's /api/v3/embed when the machine-wide
  embedding worker is owned elsewhere or memory pressure blocks spawn.
  Default on; SLM_EMBED_DAEMON_FALLBACK=0 to disable.
```

- [ ] **Step 5: 提交**

```bash
git add docs/research-2026-08-21-embeddingservice-daemon-routing.md CHANGELOG.md
git commit -m "docs: changelog + research-doc status for embedding daemon fallback"
```

---

## Self-Review 记录

- **Spec 覆盖**:spec §2 四决策 → Global Constraints + Task 2 Step 4(env 开关);§3.1 proxy 扩展 → Task 1;§3.2 fallback 属性/attach/detach → Task 2;§4 状态机与对外语义 → Task 2(判定顺序)+ Task 3(is_available/is_warm/embedder_mode/维度);§5 错误处理/超时/日志 → Task 2 Step 4(计数/分类/日志);§6 测试 1-7 → Task 2-3 单测,测试 8 → Task 4;§7 回归基线 → Task 5。
- **有意收缩(spec 偏差说明)**:spec §5 的"health/组件注册表暴露 embedder_mode"落地为 `EmbeddingService.embedder_mode` property + attach/detach 日志——engine.py 无 `health()` 字典、`component_registry.probe_embedder_model` 只探测 config 不接触活实例,接线到 doctor 需要扩大 diff;property 已向任何 health 消费方开放该信息,doctor 集成留作上游 PR 时的 review 讨论点。
- **Placeholder 扫描**:无 TBD/TODO;Task 4 的 fixture 二选一是有意的实现者选择(两版本代码均完整给出),非占位。
- **类型一致性**:`_daemon_fallback`/`_fallback_*` 命名全文一致;`McpEmbedderProxy(port, timeout, strict)` 签名在 Task 1 produces 与 Task 2 consumes 一致;`DimensionMismatchError` 从 embeddings.py 导入路径一致。
