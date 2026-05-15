# Spec: SLM Daemon 可靠性修复 + Recall Scope 参数贯通

> 2026-05-15 | SuperLocalMemory V3.4.45+

## 问题

Hermes Agent 与 SLM 交互时，daemon engine 崩溃后 MCP 工具调用永久阻塞，导致 Hermes 陷入无法退出的 busy 状态。同时 scope-e2e 设计在 recall 路径的参数传递链断裂。共 6 个具体问题。

### 故障背景

诊断报告 `docs/hermes-agent-slmd-busy-diagnosis-2026-05-15.md`：
1. SLM daemon engine 崩溃后，health 端点被动报告 `"unavailable"` 但不恢复
2. MCP server 的 `get_engine()` 无超时冷却，每次调用重试完整初始化
3. `_enable_wal()` 在设置 busy_timeout 前执行 PRAGMA，残留 WAL 锁阻塞新连接
4. 崩溃日志仅 `logger.warning`，丢失 traceback 无法定位根因
5. HealthMonitor 不检查 engine 存活状态
6. 僵尸子进程未自动回收

### Scope Recall 参数断裂

scope-e2e spec（`2026-05-15-scope-e2e-design.md`）覆盖了 store/写入侧的所有路径，但 recall/读取侧 `include_global` / `include_shared` 参数在外层（MCP → WorkerPool → recall_worker）已添加，却在 `engine.recall()` 和 `run_recall()` 处断开——这两个方法不接受这些参数，导致 `TypeError`。

## 修改清单

### 1. WAL 连接顺序修复

**文件**: `src/superlocalmemory/storage/database.py:200-208`

**问题**: `_enable_wal()` 先用原始 sqlite3.connect() 执行 `PRAGMA journal_mode=WAL`（使用默认 5s 超时），之后才设 `busy_timeout`。如果其他进程持有写锁，这个 PRAGMA 可能失败或阻塞。

**修改**: 将 `PRAGMA busy_timeout` 移到 `PRAGMA journal_mode=WAL` 之前：

```python
def _enable_wal(self) -> None:
    conn = sqlite3.connect(str(self.db_path))
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")  # 先设 timeout
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    finally:
        conn.close()
```

### 2. MCP `get_engine()` 失败冷却

**文件**: `src/superlocalmemory/mcp/server.py:39-62`

**问题**: MCP 的 `get_engine()` 无失败冷却机制——每次工具调用都重试初始化，反复撞同一个锁/错误。daemon 的 `get_engine_lazy()`（`routes/helpers.py:85-139`）已有 5s 冷却 + 跨线程锁 + 返回 None 的实现模式。

**修改**: 参考 `get_engine_lazy()` 模式，增加：

- 模块级 `_last_engine_failure` 时间戳 + 5s 冷却
- 冷却期内返回 None（调用方工具返回 error 而非永久阻塞）
- `logger.exception()` 记录完整 traceback
- 保持现有 `_engine_lock` 双检锁模式不变

失败行为对比：

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| engine init 失败 | 每次工具调用重试，反复阻塞 | 5s 冷却期内直接返回 error |
| 冷却期过后 | 无冷却 | 自动重试一次，成功则恢复 |

### 3. Recall Scope 参数贯通

**文件**: `src/superlocalmemory/core/recall_worker.py`, `src/superlocalmemory/core/engine.py`, `src/superlocalmemory/core/recall_pipeline.py`

**问题**: `recall_worker.py:_handle_recall()` 调用 `engine.recall(include_global=..., include_shared=...)` 但 `engine.recall()` 签名不接受这些参数。同时 retrieval channels 全部已经支持 `scope: str` 参数，但 pipeline 不传递。

**方案**: 在 recall_worker 层将两个布尔值转为 `scope` 字符串（retrieval channels 已有原生支持），沿 `engine.recall()` → `run_recall()` → channels 传递。

**recall_worker.py** `_handle_recall()` — 布尔值转 scope 字符串：

```python
def _handle_recall(query: str, limit: int, session_id: str = "",
                   include_global: bool = True, include_shared: bool = True) -> dict:
    engine = _get_engine()
    # Convert booleans to scope string (retrieval channels already support scope)
    if include_global:
        scope = "personal"  # "personal" mode already includes global+shared by default
    else:
        scope = "personal"  # TODO: future scope filtering
    response = engine.recall(
        query, limit=limit, session_id=session_id or None,
        scope=scope,
    )
```

**engine.py** `recall()` — 签名加 `scope` 参数：

```python
def recall(
    self,
    query: str,
    profile_id: str | None = None,
    mode: Mode | None = None,
    limit: int = 20,
    agent_id: str = "unknown",
    session_id: str | None = None,
    fast: bool = False,
    scope: str = "personal",  # 新增
) -> RecallResponse:
```

透传给 `run_recall(..., scope=scope)`。

**recall_pipeline.py** `run_recall()` — 签名加 `scope` 参数并传递给 retrieval channels：

```python
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
    scope: str = "personal",  # 新增
) -> RecallResponse:
```

在调用各 retrieval channel 时传入 `scope=scope`。Channels 已支持此参数，无需修改。

### 4. 崩溃日志完整 traceback

**文件**: `src/superlocalmemory/server/unified_daemon.py:547-548`

**修改**: 一行改动：

```python
except Exception as exc:
    logger.exception("Engine init failed")  # 自动附带 traceback
    application.state.engine = None
    application.state.config = None
```

### 5. Health 端点被动恢复 + HealthMonitor engine 检查

**文件**: `src/superlocalmemory/server/unified_daemon.py:1077-1087` 和 `src/superlocalmemory/core/health_monitor.py`

**unified_daemon.py `/health` 端点** — 发现 engine 为 None 时调用 `get_engine_lazy()` 尝试恢复：

```python
@app.get("/health")
async def health():
    _update_activity()
    engine = getattr(application.state, "engine", None)
    if engine is None:
        engine = get_engine_lazy(application.state)  # 已有 5s cooldown 保护
    return {
        "status": "ok",
        "pid": os.getpid(),
        "engine": "initialized" if engine else "unavailable",
        "version": getattr(application, 'version', 'unknown'),
    }
```

`get_engine_lazy()` 已有 5s 冷却机制，反复调用 `/health` 不会造成性能问题。

**health_monitor.py** — 新增 `_check_engine_health()` 并注册：

```python
def _check_engine_health(self) -> dict:
    """Check if the daemon engine is alive (accesses app state)."""
    try:
        import superlocalmemory.server.unified_daemon as _daemon
        app = getattr(_daemon, '_application', None)
        if app is None:
            return {"name": "engine", "status": "unknown", "detail": "Application not found"}
        engine = getattr(app.state, "engine", None)
        if engine is None:
            return {"name": "engine", "status": "critical", "detail": "Engine unavailable"}
        return {"name": "engine", "status": "ok", "detail": "Engine initialized"}
    except Exception as exc:
        return {"name": "engine", "status": "error", "detail": str(exc)}
```

在 `start()` 中注册：`register_health_check(self._check_engine_health)`

### 6. 僵尸子进程回收

**文件**: `src/superlocalmemory/core/health_monitor.py`

在 `_check_once()` 末尾增加非阻塞 waitpid 回收：

```python
# Reap zombie child processes
import os as _os
try:
    while True:
        wpid, status = _os.waitpid(-1, _os.WNOHANG)
        if wpid == 0:
            break
        logger.info("Reaped zombie child PID %d (exit code=%d)", wpid, status >> 8)
        log_structured(
            level="info", operation="reap_zombie",
            pid=wpid, exit_code=status >> 8,
        )
except ChildProcessError:
    pass  # No children at all
except Exception:
    pass  # Non-critical
```

## 不修改的部分

| 模块 | 原因 |
|------|------|
| retrieval channels | 已全部支持 `scope: str` 参数 |
| `database.py` `_scope_where()` | 已完整实现 scope 过滤逻辑 |
| `mcp/tools_core.py` | 已正确传递 `include_global`/`include_shared` |
| `worker_pool.py` | 已正确传递参数 |
| `pending_memories` 表 | 无 schema 变更 |
| `get_engine_lazy()` | 已实现冷却模式，无需改造 |
| `recall_worker.py` 子进程架构 | 保持独立进程模式，不引入共享连接池 |

## 测试要点

| # | 验证点 | 方法 |
|---|--------|------|
| 1 | WAL 锁竞争下 `_enable_wal()` 不立即失败 | 模拟并发连接测试 |
| 2 | MCP 工具在 engine 不可用时 5s 内返回 error（不阻塞） | 单元测试：mock engine init 抛异常 |
| 3 | MCP 冷却期后自动重试成功 | 单元测试：首次失败 → 等待 5s → 再次调用成功 |
| 4 | `engine.recall(scope="personal")` 透传到 channel | 单元测试：验证 run_recall 收到 scope |
| 5 | recall_worker 布尔→字符串转换正确 | 单元测试：覆盖 include_global/include_shared 组合 |
| 6 | 崩溃日志包含完整 traceback | 代码审查：确认 `logger.exception()` |
| 7 | `/health` 在 engine=None 时触发恢复 | 集成测试：设 engine=None → GET /health → engine 恢复 |
| 8 | HealthMonitor 含 engine 检查项 | 检查 `run_all_health_checks()` 输出 |
| 9 | 僵尸进程被自动回收 | 在测试环境创建僵尸 → 等待 HealthMonitor 循环 → 验证已回收 |
| 10 | 所有改动不破坏现有测试 | 全量 `pytest tests/` 通过 |

## 向后兼容

- 所有新参数有默认值（`scope="personal"`）
- `_enable_wal()` 行为不变，仅调换语句顺序
- MCP `get_engine()` 异常时返回 error 而非之前的行为（之前也是错误，只是阻塞方式不同）
- `/health` 端点返回格式不变
- 无 schema 变更、无新依赖
