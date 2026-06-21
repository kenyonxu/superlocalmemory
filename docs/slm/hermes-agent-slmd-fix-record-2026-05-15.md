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

### 9. Embed Worker 模型加载超时

**文件**: `src/superlocalmemory/core/embeddings.py:467-478`

**问题**: 环境变量 `HF_ENDPOINT=https://hf-mirror.com`（HuggingFace 镜像站）SSL 连接失败。Embed worker 子进程继承父进程环境，模型加载时 `SentenceTransformer` 向镜像站发起 HTTPS 请求，每个文件重试 5 轮（指数退避: 1s+2s+4s+8s+8s = ~23s），多文件累计超 180s 后被 SIGKILL。

**修复**: 在 `_ensure_worker()` 的子进程环境字典中 `env.pop("HF_ENDPOINT", None)`，worker 默认使用 `huggingface.co`（直连可达）。模型加载从 >180s（超时 kill）降至 ~110s（成功）。

```
commit 4ac0d5d
```

---

### 10. Materializer 实体提取

**文件**: `src/superlocalmemory/server/unified_daemon.py:1500-1524`

**问题**: Daemon 的 materializer 线程创建 `AtomicFact` 时不填充 `entities` 字段，导致 `run_store_fact_direct()` 中实体解析被跳过（条件 `if fact.entities:` 为 False），98 条旧事实无实体、无 graph edges。

**修复**: 创建 `AtomicFact` 前调用 `engine._fact_extractor.extract_facts()` 提取实体，传入 `AtomicFact(entities=entities)`。实体解析和 KG 构建管线随之激活。

```
commit 4b4375b
```

---

### 11. 旧事实实体回溯

**文件**: `src/superlocalmemory/core/engine.py:326-387`

**问题**: 98 条旧事实的 `canonical_entities_json` 均为空数组，需要一次性回溯填充。

**修复**: `MemoryEngine._backfill_entities_for_existing_facts()` — 引擎初始化时查询所有 `canonical_entities_json = '[]'` 的事实，提取实体 → 解析规范 ID → 更新行 → 构建图边。跨所有 profile 处理，幂等。

**效果**: 62/99 事实成功填充实体，生成 3 条图边。剩余 37 条事实内容中无可提取的实体提及（预期行为）。

```
commit 0b6a505, 8499653
```

---

### 12. NULL Embedding 定时回溯

**文件**: `src/superlocalmemory/core/maintenance.py:317-340`, `maintenance_scheduler.py:37-55`

**问题**: 旧事实和 materializer 创建的事实在 embed worker 就绪前已存储，`embedding` 列为 NULL，无法参与语义搜索。

**修复**: 维护调度器（30 分钟间隔）新增 `embedder` 参数，每周期处理最多 50 条 `embedding IS NULL` 的事实，批量调用 `embedder.embed_batch()` 填充。

```
commit 402ed9a
```

---

## 剩余问题

| 问题 | 状态 | 备注 |
|------|------|------|
| Recall 按 scope 过滤 | Deferred (scope-r2) | 当前 `include_global`/`include_shared` 保留签名但未启用区分语义 |
| `test_recall_with_all_channels_mock` 失败 | 预存 | Channel mock 未更新以接收 `scope` 参数，非本次引入 |

---

## 合并记录

```
4b4375b fix: extract entities in daemon materializer for KG edge building
402ed9a feat: backfill NULL embeddings during scheduled maintenance
8499653 fix: backfill entities across all profiles, not just engine profile
0b6a505 feat: backfill entities + graph edges for existing facts on engine init
4ac0d5d fix: remove HF_ENDPOINT from embedding worker environment
7592179 docs: update fix record with embed worker HF_ENDPOINT fix
6b7aa65 feat: reap zombie child processes in HealthMonitor._check_once()
b617819 feat: add engine recovery to /health endpoint and health check
f75600a fix: add scope parameter to engine.recall()/run_recall() to fix TypeError
bb7c811 feat: add 5s failure cooldown to MCP get_engine()
deec6e0 fix: use logger.exception for engine init failure to capture traceback
b8f847f fix: set busy_timeout before journal_mode=WAL in _enable_wal
6930709 chore: add .worktrees to gitignore
```

全部已合并至 `main` 并推送至 `origin/main`。
