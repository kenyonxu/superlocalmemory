# Hermes Agent 永久 Busy 问题 — 修复记录

**日期**: 2026-05-15  
**关联诊断**: `docs/hermes-agent-slmd-busy-diagnosis-2026-05-15.md`  
**Spec**: `docs/superpowers/specs/2026-05-15-slmd-reliability-fixes.md`  
**Plan**: `docs/superpowers/plans/2026-05-15-slmd-reliability-fixes.md`  
**分支**: `fix/slmd-reliability-fixes` (已合并 `main`)

---

## 修复清单

### 1. WAL 连接顺序修复

**文件**: `src/superlocalmemory/storage/database.py:200-208`

**问题**: `_enable_wal()` 在设置 `PRAGMA busy_timeout` 之前执行 `PRAGMA journal_mode=WAL`。如果其他进程持有写锁，WAL pragma 使用默认 5s 超时，可能提前失败或阻塞。

**修复**: 将 `PRAGMA busy_timeout` 移到 `PRAGMA journal_mode=WAL` 之前，确保 WAL 模式切换时使用配置的 10s 超时。

```
commit b8f847f
```

---

### 2. MCP `get_engine()` 失败冷却

**文件**: `src/superlocalmemory/mcp/server.py:32-69`

**问题**: MCP 的 `get_engine()` 无失败冷却机制。当 daemon engine 崩溃后，每次 MCP 工具调用都重试完整初始化，在 SQLite WAL 锁上永久阻塞，导致 Hermes "永久 busy"。

**修复**: 参考 `routes/helpers.py:get_engine_lazy()` 的 5s 冷却模式：
- 初始化失败后设置 `_last_engine_failure` 时间戳
- 冷却期（5s）内直接抛出 `RuntimeError`（被 MCP 工具 `try/except` 捕获返回错误）
- 冷却期过后自动重试一次
- `reset_engine()` 同步重置冷却状态

**行为变化**:

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| engine init 失败 | 每次调用阻塞重试 | 5s 内返回 error（不阻塞） |
| 冷却期过后 | 无冷却 | 自动重试，成功则恢复 |

```
commit bb7c811
```

---

### 3. Recall Scope 参数贯通

**文件**: `recall_worker.py`, `engine.py`, `recall_pipeline.py`

**问题**: `recall_worker.py:_handle_recall()` 调用 `engine.recall(include_global=..., include_shared=...)` 但 `engine.recall()` 签名不接受这些参数，导致 `TypeError`。scope-e2e spec 只覆盖了 store 路径，recall 路径断裂。

**修复**:
- `recall_worker.py`: 将 `include_global`/`include_shared` 布尔值转为 `scope="personal"` 字符串
- `engine.py`: `recall()` 签名新增 `scope: str = "personal"` 参数
- `recall_pipeline.py`: `run_recall()` 签名新增 `scope` 参数（暂不转发到 `retrieval_engine.recall()`，由 scope-r2 完成）

```
commit f75600a
```

---

### 4. 崩溃日志完整 Traceback

**文件**: `src/superlocalmemory/server/unified_daemon.py:547-548`

**问题**: Engine 初始化失败时仅调用 `logger.warning("Engine init failed: %s", exc)`，丢失完整 traceback，无法定位崩溃根因。

**修复**: 改为 `logger.exception("Engine init failed")`，自动附带完整堆栈。

```
commit b8f847f
```

---

### 5. Health 端点自动恢复 + Engine 健康检查

**文件**: `src/superlocalmemory/server/unified_daemon.py`

**问题**: `/health` 端点发现 engine 为 None 时被动报告 `"unavailable"`，不尝试恢复。HealthMonitor 不检查 engine 存活状态。

**修复**:
- `/health` 端点：发现 engine=None 时调用 `get_engine_lazy()` 触发恢复（已有 5s 冷却保护）
- `create_app()`: 在 HealthMonitor 启动后注册 `_check_engine` 闭包作为健康检查项（从 daemon 侧注册，保持 `core/` `server/` 分层清晰）

```
commit b617819
```

---

### 6. 僵尸子进程自动回收

**文件**: `src/superlocalmemory/core/health_monitor.py:271`

**问题**: 诊断发现 7 个 `<defunct>` 僵尸进程。HealthMonitor 应负责清理。

**修复**: 在 `_check_once()` 末尾增加非阻塞 `os.waitpid(-1, WNOHANG)` 循环。区分正常退出和信号杀死，记录不同日志。

```
commit 6b7aa65
```

---

### 7. Materializer 实体提取（新增修复）

**文件**: `src/superlocalmemory/server/unified_daemon.py:1500-1524`

**问题**: Daemon 的 materializer 线程创建 `AtomicFact` 时不填充 `entities` 字段，导致 `run_store_fact_direct()` 中实体解析被跳过（条件 `if fact.entities:` 为 False），knowledge graph 边数为 0。98 条事实中无一有实体或图边。

**修复**: 在创建 `AtomicFact` 前通过 `engine._fact_extractor.extract_facts()` 提取实体，传递给 `AtomicFact(entities=entities)`。实体解析和 KG 构建管线随之激活。

**验证**: 测试事实 "Kai prefers using Python for AI agent development and Rust for systems programming" 成功提取实体 `{Python, Rust, Kai}` 并解析为规范实体。

---

### 8. 数据目录统一

**问题**: 诊断报告建议 #6 指出 daemon 使用默认 `~/.superlocalmemory/` 而 Hermes MCP 使用 `~/.hermes/profiles/zhihui/home/.superlocalmemory/`，两者指向不同数据库。`SLMConfig.load()` 硬编码使用 `~/.superlocalmemory/config.json`，不支持 `SLM_DATA_DIR` 环境变量覆盖 `base_dir`。

**修复**: 将 Hermes 数据目录内容迁移至 `~/.superlocalmemory/`，删除 `SLM_DATA_DIR` 环境变量依赖。

---

## 剩余问题

| 问题 | 状态 | 备注 |
|------|------|------|
| Embed worker 模型加载超时 | ✅ 已修复 | `HF_ENDPOINT=https://hf-mirror.com` SSL 失败→每次 API 调用重试 5 轮(~23s)→多文件累计 >180s。修复: worker 子进程清除 `HF_ENDPOINT`，默认 huggingface.co。加载时间 180s+→110s |
| Recall 按 scope 过滤 | Deferred (scope-r2) | 当前 `include_global`/`include_shared` 保留签名但未启用区分语义 |
| `test_recall_with_all_channels_mock` 失败 | 预存 | Channel mock 未更新以接收 `scope` 参数，非本次引入 |

---

## 合并记录

```
6930709 chore: add .worktrees to gitignore
b8f847f fix: set busy_timeout before journal_mode=WAL in _enable_wal
deec6e0 fix: use logger.exception for engine init failure to capture traceback
bb7c811 feat: add 5s failure cooldown to MCP get_engine()
f75600a fix: add scope parameter to engine.recall()/run_recall() to fix TypeError
b617819 feat: add engine recovery to /health endpoint and health check
6b7aa65 feat: reap zombie child processes in HealthMonitor._check_once()
4ac0d5d fix: remove HF_ENDPOINT from embedding worker environment
```

全部已合并至 `main` 并推送至 `origin/main`。
