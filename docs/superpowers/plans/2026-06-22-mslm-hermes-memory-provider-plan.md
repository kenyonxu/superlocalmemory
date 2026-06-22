# MSLM Hermes MemoryProvider 实现计划 v1

> **对应 SPEC**: [2026-06-02-mslm-hermes-memory-provider.md](../specs/2026-06-02-mslm-hermes-memory-provider.md)  
> **计划日期**: 2026-06-22  
> **预计工期**: 5 个工作日（TDD 节奏）  
> **总代码量**: ~450 行（`__init__.py`）+ ~200 行测试 + `plugin.yaml`  

---

## 目录

1. [实现策略概览](#1-实现策略概览)
2. [Chunk 划分](#2-chunk-划分)
3. [验收标准](#3-验收标准)
4. [风险与回退](#4-风险与回退)
5. [附录：Commit 语义规范](#5-附录commit-语义规范)

---

## 1. 实现策略概览

### 1.1 核心原则

- **TDD 先行**: 每个 Chunk 先写测试 → 再写代码 → 再重构，测试通过才进入下一 Chunk
- **独立交付**: 每个 Chunk 可独立 review、合并，不阻塞后续工作
- **SPEC 对齐**: 严格遵循 2026-06-22 审阅修复后的线程安全设计（`_sync_turn_lock`、`_init_cancelled`、`_parse_bool`）
- **渐进暴露**: v1 交付 3 个工具（recall/remember/status），`slm_report_feedback` 留 v2

### 1.2 文件结构

```
superlocalmemory/
└── src/superlocalmemory/integrations/hermes/
    ├── __init__.py              # 主实现 (~450 行)
    ├── plugin.yaml              # 元数据清单
    └── tests/
        ├── conftest.py          # pytest fixture: mock MemoryEngine, mock SLMConfig
        ├── test_provider.py     # 生命周期 + 工具集成测试
        ├── test_threading.py    # 线程安全专项测试
        └── test_tools.py        # 工具调用单元测试
```

### 1.3 依赖矩阵

| Chunk | 依赖 Chunk | 说明 |
|-------|-----------|------|
| 1 | 无 | 骨架 + 配置解析，纯静态方法 |
| 2 | 1 | 需要 `initialize()` 完成后的 provider 实例 |
| 3 | 2 | 需要 engine mock 支持 `recall()` / `store()` |
| 4 | 2 | 需要 engine mock 支持 `store()` |
| 5 | 2,3 | 需要完整 provider 实例 + 工具 schema |

---

## 2. Chunk 划分

### Chunk 1: 骨架与配置解析（Day 1）

**目标**: 搭建 `SuperLocalMemoryProvider` 类骨架，完成配置加载和 `is_available()`。

**测试先行**:

```python
# tests/test_provider.py
class TestProviderSkeleton:
    def test_is_available_when_import_fails(self):
        """当 superlocalmemory 不可 import 时返回 False"""
    
    def test_is_available_when_import_succeeds(self):
        """当 superlocalmemory 可 import 时返回 True"""
    
    def test_name_property(self):
        """name 返回 'superlocalmemory'"""
    
    def test_get_config_schema_returns_expected_keys(self):
        """schema 包含 mslm_profile, mode, include_global, include_shared"""
    
    def test_parse_bool_with_various_inputs(self):
        """_parse_bool 正确处理 None, bool, str, int 类型"""
        # 覆盖: None→default, True→True, False→False
        # 覆盖: "true"→True, "false"→False, "1"→True, "0"→False
        # 覆盖: "yes"→True, "no"→False, "on"→True, "off"→False
        # 覆盖: 1→True, 0→False
```

**实现内容**:

1. 类声明 + `__init__` 字段初始化（全部设为 `None`/`False`/`""`）
2. `name` property → `"superlocalmemory"`
3. `is_available()` → 尝试 `import superlocalmemory`，捕获 `ImportError`
4. `get_config_schema()` → 返回 4 个配置项的 schema 列表
5. 静态方法 `_parse_bool(value, default)` → 类型安全解析
6. `_load_hermes_config(hermes_home)` → 读取 `~/.hermes/config.yaml` 的 `memory.superlocalmemory` section

**Commit 消息**:
```
feat(hermes): add SuperLocalMemoryProvider skeleton + config parsing

- Implement is_available() with graceful ImportError handling
- Add get_config_schema() exposing 4 config keys
- Add _parse_bool() for type-safe YAML boolean parsing
- Add _load_hermes_config() to read Hermes config overrides
```

**验收点**:
- [ ] `is_available()` 在 `superlocalmemory` 未安装时返回 `False`
- [ ] `is_available()` 在已安装时返回 `True`
- [ ] `_parse_bool("false", True)` → `False`（关键 bug 防护）
- [ ] `_parse_bool("true", False)` → `True`
- [ ] schema 包含 `mslm_profile`, `mode`, `include_global`, `include_shared`

---

### Chunk 2: 初始化与引擎生命周期（Day 1-2）

**目标**: 实现 `initialize()` 完整流程，包括超时保护、取消标志、speaker entities 创建。

**测试先行**:

```python
class TestInitialize:
    def test_initialize_loads_config_and_sets_profile(self):
        """从 kwargs['agent_identity'] 映射 profile"""
    
    def test_initialize_uses_config_override(self):
        """Hermes config 中的 mslm_profile 覆盖 agent_identity"""
    
    def test_initialize_sets_mode_from_override(self):
        """config.yaml 中的 mode 覆盖 MSLM 默认值"""
    
    def test_initialize_cron_context_skips(self):
        """agent_context='cron' 时设置 _cron_skipped=True，不创建 engine"""
    
    def test_initialize_timeout_cleans_up(self):
        """engine.initialize() 超时后设置 _init_cancelled，释放 _engine"""
    
    def test_initialize_exception_disables_provider(self):
        """engine.initialize() 抛异常后 _engine = None"""
    
    def test_initialize_creates_speaker_entities(self):
        """成功初始化后调用 create_speaker_entities('user', 'hermes')"""
    
    def test_initialize_speaker_entities_non_fatal(self):
        """create_speaker_entities 失败不中断初始化"""
    
    def test_parse_bool_applied_to_include_global(self):
        """include_global 通过 _parse_bool 解析"""
    
    def test_parse_bool_applied_to_include_shared(self):
        """include_shared 通过 _parse_bool 解析"""
```

**实现内容**:

1. `initialize(session_id, **kwargs)` 完整流程：
   - 解析 `mslm_profile`（override → `agent_identity` → `"default"`）
   - 加载 `SLMConfig`，设置 `active_profile`
   - 应用 `mode` override（需处理 `KeyError`）
   - 用 `_parse_bool` 解析 `include_global` / `include_shared`
   - cron 守卫（`agent_context in {"cron", "flush"}` 或 `platform == "cron"`）
   - 创建 `MemoryEngine`，线程 + 30s timeout 初始化
   - 设置 `_init_cancelled` 标志，超时后清理
   - 调用 `create_speaker_entities()`，非致命错误处理
   - 记录 `logger.info` 就绪状态

2. `_ensure_engine()` → 检查 `self._engine is not None`

3. `shutdown()` → 等待后台线程 + 清理 engine 引用

**Commit 消息**:
```
feat(hermes): implement initialize() with timeout and cancellation

- Add 30s timeout guard for engine.initialize() via daemon thread
- Add _init_cancelled flag for graceful cleanup on timeout
- Add cron context skip (_cron_skipped)
- Add create_speaker_entities with non-fatal error handling
- Add _ensure_engine() health check helper
- Add shutdown() for resource cleanup
```

**验收点**:
- [ ] 正常路径：`initialize()` 后 `_engine` 不为 `None`，`_session_id` 已设置
- [ ] 超时路径：30s 超时后 `_init_cancelled=True`，`_engine=None`，无内存泄漏
- [ ] cron 路径：`agent_context="cron"` 时 `_cron_skipped=True`，不加载模型
- [ ] 异常路径：`MemoryEngine` 创建失败时 `_engine=None`，不抛异常到上层
- [ ] `create_speaker_entities` 失败时初始化继续完成

---

### Chunk 3: prefetch 混合模式（Day 2）

**目标**: 实现 `prefetch()`（首次同步、后续消费缓存）和 `queue_prefetch()`（后台预取）。

**测试先行**:

```python
class TestPrefetch:
    def test_prefetch_first_turn_sync_recall(self):
        """Turn 1: 无缓存，同步调用 engine.recall()"""
    
    def test_prefetch_subsequent_turn_uses_cache(self):
        """Turn 2: 消费 _prefetch_cache，不调用 engine.recall()"""
    
    def test_prefetch_empty_query_returns_empty(self):
        """query 为空时直接返回 ''"""
    
    def test_prefetch_engine_none_returns_empty(self):
        """engine 未初始化时返回 ''"""
    
    def test_prefetch_cron_skipped_returns_empty(self):
        """_cron_skipped=True 时返回 ''"""
    
    def test_queue_prefetch_starts_background_thread(self):
        """queue_prefetch 启动 daemon thread 调用 engine.recall()"""
    
    def test_queue_prefetch_writes_cache(self):
        """后台线程完成后 _prefetch_cache 被写入"""
    
    def test_queue_prefetch_concurrent_safety(self):
        """连续调用 queue_prefetch 不会启动重叠线程"""
    
    def test_prefetch_lock_protects_cache(self):
        """_prefetch_lock 保护缓存读写"""
```

**实现内容**:

1. `prefetch(query)`:
   - 检查 `_cron_skipped` / `_engine is None` / 空 query → 返回 `""`
   - 首次（无缓存或 `_prefetch_fired_at` 不匹配）：同步 `engine.recall(query, limit=8, fast=True)`，8s 超时
   - 后续：消费 `_prefetch_cache`，用 `_prefetch_lock` 保护读写
   - 格式化结果（`_format_recall_results()`）

2. `queue_prefetch(query)`:
   - 启动 daemon thread 执行 `engine.recall(query, limit=8, fast=True)`
   - 结果写入 `_prefetch_cache`，更新 `_prefetch_fired_at`
   - 用 `_prefetch_lock` 保护缓存写入

3. `_format_recall_results(results)` → 将 recall 结果格式化为 prompt 文本

4. `_sync_recall(query, **kwargs)` → 同步 recall 包装，带异常处理

**Commit 消息**:
```
feat(hermes): implement prefetch hybrid mode

- First turn: synchronous engine.recall() with 8s timeout
- Subsequent turns: consume _prefetch_cache from prior queue_prefetch
- Add queue_prefetch() with daemon thread for background recall
- Add _prefetch_lock for cache thread safety
- Add _format_recall_results() for prompt injection formatting
- Add _sync_recall() wrapper with exception handling
```

**验收点**:
- [ ] Turn 1：`prefetch()` 直接调用 `engine.recall()`，返回格式化结果
- [ ] Turn 2：`prefetch()` 读取 `_prefetch_cache`，不重复调用 `engine.recall()`
- [ ] `queue_prefetch()` 启动后台线程，线程结束后缓存可用
- [ ] 空 query / engine None / cron skipped → 返回 `""`，不抛异常
- [ ] 缓存读写受 `_prefetch_lock` 保护

---

### Chunk 4: sync_turn 与钩子（Day 3）

**目标**: 实现 `sync_turn()`（合并存储）、`on_memory_write()`、`on_pre_compress()`、`on_session_switch()`、`on_session_end()`。

**测试先行**:

```python
class TestSyncTurn:
    def test_sync_turn_stores_combined_content(self):
        """合并 user + assistant 内容，调用 engine.store()"""
    
    def test_sync_turn_skips_short_meaningless(self):
        """跳过 'ok', 'yes', 'thanks', 'thx' 等无意义回复"""
    
    def test_sync_turn_uses_write_lock(self):
        """engine.store() 在 _write_lock 保护下执行"""
    
    def test_sync_turn_uses_sync_turn_lock(self):
        """is_alive() 检查和 thread.start() 受 _sync_turn_lock 保护"""
    
    def test_sync_turn_drops_when_prior_incomplete(self):
        """上一轮写入未完成时，跳过本轮写入"""
    
    def test_sync_turn_truncates_long_content(self):
        """>4000 字符时截断到 4000"""
    
    def test_sync_turn_cron_skipped(self):
        """_cron_skipped=True 时直接返回"""
    
    def test_sync_turn_engine_none(self):
        """_engine=None 时直接返回"""

class TestHooks:
    def test_on_memory_write_calls_store(self):
        """内置 memory 写入镜像到 MSLM"""
    
    def test_on_pre_compress_stores_last_10_messages(self):
        """取最后 10 条消息拼接，存入 MSLM"""
    
    def test_on_pre_compress_returns_empty_string(self):
        """返回 '' 不干扰 compression summary"""
    
    def test_on_pre_compress_skips_empty_or_non_text(self):
        """跳过空内容、非 user/assistant 角色、非字符串 content"""
    
    def test_on_session_end_calls_close_session(self):
        """调用 engine.close_session(session_id)"""
    
    def test_on_session_switch_updates_session_id(self):
        """更新 _session_id，清空 _prefetch_cache"""
```

**实现内容**:

1. `sync_turn(user_content, assistant_content, *, session_id="")`:
   - 检查 `_cron_skipped` / `_engine is None` → 返回
   - sanitize + 语义过滤（跳过 `"ok"`, `"yes"`, `"thanks"`, `"thx"`）
   - 合并为 `User: ...\nHermes: ...` 格式
   - >4000 字符截断
   - `_sync_turn_lock` 保护 is_alive 检查和 thread 创建
   - `_write_lock` 保护 `engine.store()`
   - 上一轮未完成 → 跳过本轮（丢弃策略）
   - daemon thread 后台写入

2. `on_memory_write(action, target, content)`:
   - 调用 `engine.store(content, scope="personal")`（受 `_write_lock`）

3. `on_pre_compress(messages)`:
   - 取最后 10 条 `user`/`assistant` 消息
   - 拼接为 `[Pre-compression context]\nrole: content...`
   - 每条 content 截断到 500 字符
   - 后台 thread 存入 MSLM（受 `_write_lock`）
   - 返回 `""`（不干扰 compression）

4. `on_session_end(messages)`:
   - 调用 `engine.close_session(self._session_id)`

5. `on_session_switch(new_id, **kwargs)`:
   - 更新 `self._session_id = new_id`
   - 清空 `_prefetch_cache`

**Commit 消息**:
```
feat(hermes): implement sync_turn and lifecycle hooks

- Add sync_turn() with merged storage, semantic filtering, 4000-char truncation
- Add _sync_turn_lock to prevent race between is_alive() and thread.start()
- Add _write_lock shared across all write paths (sync_turn, on_memory_write, on_pre_compress)
- Add on_memory_write() mirror to MSLM
- Add on_pre_compress() storing last 10 messages, returning ''
- Add on_session_end() calling engine.close_session()
- Add on_session_switch() clearing prefetch cache
```

**验收点**:
- [ ] `sync_turn()` 合并存储 `User: ...\nHermes: ...`
- [ ] 语义过滤： `"ok"` / `"yes"` / `"thanks"` / `"thx"` → 跳过存储
- [ ] `_sync_turn_lock` 防止竞态：两个 turn 快速连续时不会创建两个写线程
- [ ] `_write_lock` 串行化所有 `engine.store()` 调用
- [ ] 上一轮写入未完成时，本轮丢弃（不堆积队列）
- [ ] >4000 字符截断到 4000
- [ ] `on_pre_compress()` 取最后 10 条，每条截断 500 字符，返回 `""`
- [ ] `on_session_end()` 调用 `engine.close_session()`
- [ ] `on_session_switch()` 更新 session_id，清空缓存

---

### Chunk 5: 工具实现（Day 4-5）

**目标**: 实现 `slm_recall`、`slm_remember`、`slm_status` 三个工具（v1 砍掉 `slm_report_feedback`）。

**测试先行**:

```python
class TestToolSchemas:
    def test_get_tool_schemas_returns_three_tools(self):
        """返回 recall, remember, status 三个 schema"""
    
    def test_recall_schema_has_required_query(self):
        """slm_recall 的 query 为 required"""
    
    def test_remember_schema_has_optional_scope(self):
        """slm_remember 的 scope 默认 'personal'"""

class TestToolRecall:
    def test_recall_routes_to_engine_recall(self):
        """调用 engine.recall()，返回格式化结果"""
    
    def test_recall_empty_query_returns_error(self):
        """query 为空返回 tool_error"""
    
    def test_recall_engine_not_ready_returns_error(self):
        """engine 未初始化返回 tool_error"""
    
    def test_recall_limit_capped_at_20(self):
        """limit > 20 时截断到 20"""
    
    def test_recall_respects_include_global(self):
        """调用 recall 时传入 include_global 配置"""

class TestToolRemember:
    def test_remember_calls_engine_store(self):
        """调用 engine.store()，返回 stored 状态"""
    
    def test_remember_default_scope_personal(self):
        """默认 scope='personal'"""
    
    def test_remember_global_scope(self):
        """scope='global' 时传入 global"""
    
    def test_remember_no_facts_returns_noop(self):
        """engine.store() 返回空 fact_ids 时返回 noop"""
    
    def test_remember_engine_not_ready_returns_error(self):
        """engine 未初始化返回 tool_error"""

class TestToolStatus:
    def test_status_returns_profile_and_counts(self):
        """返回 profile, mode, facts, entities, db_size 等"""
    
    def test_status_engine_not_ready_returns_error(self):
        """engine 未初始化返回 tool_error"""
    
    def test_status_v1_returns_total_only(self):
        """v1 只返回 facts.total，不细分 lifecycle"""

class TestHandleToolCall:
    def test_routes_recall_to_tool_recall(self):
        """工具名 'slm_recall' 路由到 _tool_recall"""
    
    def test_routes_remember_to_tool_remember(self):
        """工具名 'slm_remember' 路由到 _tool_remember"""
    
    def test_routes_status_to_tool_status(self):
        """工具名 'slm_status' 路由到 _tool_status"""
    
    def test_unknown_tool_returns_error(self):
        """未知工具名返回 tool_error"""
    
    def test_exception_in_tool_returns_error(self):
        """工具内部异常被捕获，返回 tool_error，不中断主流程"""
```

**实现内容**:

1. `get_tool_schemas()` → 返回 3 个工具的 JSON schema

2. `handle_tool_call(tool_name, params)` → 路由到 `_tool_*`，异常捕获返回 `tool_error`

3. `_tool_recall(params)`:
   - 检查 `query` 非空
   - 调用 `engine.recall(query, limit=min(limit, 20), fast=fast, include_global=self._include_global)`
   - 格式化结果（含 `fact_id`, `content`, `score`, `confidence`, `channel_scores`）
   - 返回 JSON 结构

4. `_tool_remember(params)`:
   - 检查 `content` 非空
   - `scope` 参数校验（`"personal"` 或 `"global"`，默认 `"personal"`）
   - 调用 `engine.store(content, scope=scope)`
   - 返回 `stored` 或 `noop` 状态

5. `_tool_status(params)`:
   - 通过 `engine.db` 查询统计（v1 临时方案）
   - 返回 `profile`, `mode`, `facts.total`, `entities`, `graph_edges`, `db_size_mb`, `embedding_model`, `embedding_dim`

6. `system_prompt_block()` → 返回静态状态文本（含 profile、mode、fact_count、工具简介）

**Commit 消息**:
```
feat(hermes): implement slm_recall, slm_remember, slm_status tools

- Add get_tool_schemas() returning 3 tool definitions
- Add handle_tool_call() with exception-safe routing
- Add _tool_recall() with limit capping, include_global support
- Add _tool_remember() with scope validation (personal/global)
- Add _tool_status() querying engine.db for v1 stats
- Add system_prompt_block() with dynamic profile/mode/fact_count
- Remove slm_report_feedback from v1 (restored in v2 per spec)
```

**验收点**:
- [ ] `slm_recall` 空 query → `tool_error("query is required")`
- [ ] `slm_recall` limit > 20 → 截断到 20
- [ ] `slm_recall` 返回结果包含 `fact_id`, `content`, `score`, `confidence`
- [ ] `slm_remember` 默认 `scope="personal"`
- [ ] `slm_remember` `scope="global"` 时传入 global
- [ ] `slm_remember` 无新事实 → `{"status": "noop"}`
- [ ] `slm_status` 返回 `profile`, `mode`, `facts.total`, `entities`, `db_size_mb`
- [ ] `handle_tool_call` 异常捕获 → `tool_error`，不中断主流程
- [ ] `system_prompt_block()` 包含动态 `profile_name`, `mode`, `fact_count`

---

### Chunk 6: 集成测试与 plugin.yaml（Day 5）

**目标**: 端到端集成测试、plugin.yaml 编写、文档补全。

**测试先行**:

```python
class TestIntegration:
    def test_full_turn_lifecycle(self):
        """完整 turn: initialize → prefetch → sync_turn → queue_prefetch → on_session_end"""
    
    def test_concurrent_sync_turn_and_prefetch(self):
        """sync_turn 写入和 prefetch 读取并发，验证 WAL 一写多读"""
    
    def test_memory_write_mirror(self):
        """内置 memory tool 写入后，on_memory_write 镜像到 MSLM"""
    
    def test_provider_disabled_gracefully(self):
        """engine 初始化失败后，所有方法静默返回不抛异常"""

class TestPluginYAML:
    def test_plugin_yaml_valid(self):
        """plugin.yaml 可被解析，包含正确字段"""
```

**实现内容**:

1. `plugin.yaml`:
   - `name: superlocalmemory`
   - `version: 1.0.0`
   - `pip_dependencies: [mslm-memory>=4.0.0]`
   - `hooks: [on_session_end, on_memory_write, on_pre_compress, on_session_switch]`

2. `register(ctx)` → 插件入口，将 provider 注册到 Hermes

3. 集成测试覆盖完整生命周期

4. 文档：README 片段（安装、配置、使用示例）

**Commit 消息**:
```
feat(hermes): add plugin.yaml, register() entry point, and integration tests

- Add plugin.yaml with hooks and pip dependencies
- Add register() function for Hermes plugin discovery
- Add integration tests covering full session lifecycle
- Add concurrent read/write safety verification
- Add README with setup and usage examples
```

**验收点**:
- [ ] `plugin.yaml` 可被 Hermes 加载解析
- [ ] `register()` 正确注册 provider
- [ ] 集成测试通过：完整 session 生命周期无异常
- [ ] 并发测试通过：sync_turn 写入 + prefetch 读取不冲突
- [ ] provider 禁用后：所有方法静默返回，不抛异常到上层

---

## 3. 验收标准

### 3.1 功能验收

| # | 验收项 | 验证方式 |
|---|--------|---------|
| 1 | `is_available()` 正确检测 `superlocalmemory` 可用性 | 单元测试 |
| 2 | `initialize()` 完成 engine 初始化，30s 超时保护 | 单元测试 + 手动（慢路径） |
| 3 | `_init_cancelled` 在超时后正确清理资源 | 单元测试（mock 超时） |
| 4 | cron/flush 上下文跳过初始化，不加载模型 | 单元测试 |
| 5 | `prefetch()` Turn 1 同步，Turn 2+ 消费缓存 | 单元测试 |
| 6 | `queue_prefetch()` 后台线程不阻塞主流程 | 单元测试 |
| 7 | `sync_turn()` 合并存储，语义过滤，4000 截断 | 单元测试 |
| 8 | `_sync_turn_lock` 防止竞态创建多个写线程 | 单元测试（模拟快速连续 turn） |
| 9 | `_write_lock` 串行化所有 engine.store() | 单元测试 + 线程 dump 验证 |
| 10 | `on_pre_compress()` 取 10 条消息，返回 `""` | 单元测试 |
| 11 | `on_session_end()` 调用 `close_session()` | 单元测试 |
| 12 | `on_session_switch()` 清空缓存 | 单元测试 |
| 13 | `slm_recall` 工具返回正确格式 | 单元测试 |
| 14 | `slm_remember` 工具支持 personal/global scope | 单元测试 |
| 15 | `slm_status` 工具返回统计信息 | 单元测试 |
| 16 | `handle_tool_call` 异常捕获不中断主流程 | 单元测试（mock 抛异常） |
| 17 | `system_prompt_block()` 包含动态状态 | 单元测试 |
| 18 | `_parse_bool()` 类型安全处理所有 YAML 形式 | 单元测试 |
| 19 | 配置加载优先级：Hermes config > MSLM config > 默认值 | 单元测试 |
| 20 | plugin.yaml 可被 Hermes 识别加载 | 手动测试 |

### 3.2 性能验收

| # | 验收项 | 目标值 | 验证方式 |
|---|--------|--------|---------|
| 1 | `initialize()` 正常完成时间 | < 15s | 手动测试 |
| 2 | `initialize()` 超时后清理 | < 5s 额外等待 | 单元测试 |
| 3 | `prefetch()` 同步 recall 超时 | < 8s | 单元测试（mock） |
| 4 | `sync_turn()` 后台写入不阻塞 | < 1ms 主线程 | 单元测试 |
| 5 | `queue_prefetch()` 后台 recall | 不阻塞主线程 | 单元测试 |
| 6 | 内存占用（engine 初始化后） | ~2GB（sentence-transformers） | 手动测试 |

### 3.3 安全与健壮性验收

| # | 验收项 | 验证方式 |
|---|--------|---------|
| 1 | engine 初始化失败后所有方法静默返回 | 单元测试 |
| 2 | 工具调用异常被捕获，返回 `tool_error` | 单元测试 |
| 3 | 超长内容截断，不触发 MSLM 提取质量下降 | 单元测试 |
| 4 | 空 query / 空 content 被优雅处理 | 单元测试 |
| 5 | 线程锁无死锁（所有锁获取带超时或短持有） | 代码审查 + 单元测试 |
| 6 | daemon thread 不泄漏（正常完成/超时/异常） | 单元测试 + 线程 dump |

---

## 4. 风险与回退

### 4.1 已知风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| MSLM `engine.recall()` 内部有写操作（非纯读） | 中 | 与 `_write_lock` 冲突或 WAL 锁 | Chunk 3 测试中加入 `recall` 并发调用验证；如确认有写，将 `recall` 也纳入 `_write_lock` 或文档明确 |
| `engine.db` 直接访问 schema 变更（v1 临时方案） | 高 | `slm_status` 工具失效 | v2 优先迁移到 `engine.get_status()`；v1 中 schema 变更时同步修复 |
| sentence-transformers 模型加载 > 30s（慢磁盘） | 低 | 初始化超时，provider 禁用 | 增加 timeout 到 60s 或支持异步后台加载后通知 |
| Hermes MemoryProvider ABC 接口变更 | 低 | 编译/运行失败 | 锁定 Hermes 版本，CI 中集成测试 |
| SQLite WAL 模式下并发写冲突 | 低 | `database is locked` | `_write_lock` 已串行化所有写；如仍冲突，考虑连接级排他锁 |

### 4.2 回退策略

- **v1 最小可用**: 如果 Chunk 5 工具实现受阻，可先交付 Chunk 1-4（生命周期完整，工具 stub 返回错误），保证 Hermes 启动不崩溃
- **provider 禁用**: 任何初始化失败 → `_engine = None` → 所有方法静默返回，Hermes 降级到 Builtin MEMORY.md 模式
- **工具降级**: `slm_status` 如 `engine.db` 不可用，返回 `tool_error("status unavailable in v1")`

---

## 5. 附录：Commit 语义规范

每个 Chunk 对应一个独立 commit，消息格式：

```
<type>(hermes): <imperative verb phrase>

- <bullet point describing change>
- <bullet point describing change>
```

| Chunk | type | 示例 |
|-------|------|------|
| 1 | `feat` | `feat(hermes): add SuperLocalMemoryProvider skeleton + config parsing` |
| 2 | `feat` | `feat(hermes): implement initialize() with timeout and cancellation` |
| 3 | `feat` | `feat(hermes): implement prefetch hybrid mode` |
| 4 | `feat` | `feat(hermes): implement sync_turn and lifecycle hooks` |
| 5 | `feat` | `feat(hermes): implement slm_recall, slm_remember, slm_status tools` |
| 6 | `feat` | `feat(hermes): add plugin.yaml, register() entry point, and integration tests` |
| 修复 | `fix` | `fix(hermes): handle race condition in sync_turn thread creation` |
| 测试 | `test` | `test(hermes): add concurrent read/write safety verification` |
| 文档 | `docs` | `docs(hermes): add README with setup and usage examples` |

---

*计划完成。主人请审阅，如有调整知惠随时修改。*
