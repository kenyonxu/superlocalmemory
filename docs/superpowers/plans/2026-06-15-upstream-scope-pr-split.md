# Upstream Multi-Scope PR 拆分实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 multi-scope memory 功能拆分为 3 个独立、干净、可单独审查的 PR 提交到上游 `qualixar/superlocalmemory`。

**Architecture:** 使用 git worktree 为每个 PR 创建隔离环境，从 `upstream/main` 干净分支出发，逐层 cherry-pick/应用变更。每个 PR 独立可测、独立可审查。PR-A（Schema + Migration）→ PR-B（Retrieval）→ PR-C（Interface），形成依赖链。

**Tech Stack:** Git worktree, Python, pytest

---

## 前置条件

- [ ] PR #24 (WAL busy_timeout) 和 PR #25 (logger.exception) 被上游合并
- [ ] `upstream/main` 已拉取最新
- [ ] 本地 master 分支已 rebase 到 `upstream/main`

---

## Chunk 1: PR-A — Schema + Migration（~130 LOC）

### 目标

仅包含存储层变更：8 个核心表新增 `scope` + `shared_with` 列、数据模型更新、数据库迁移脚本。不涉及任何检索逻辑或外部接口变更。

### 文件清单

| 文件 | 操作 | LOC | 说明 |
|------|------|-----|------|
| `src/superlocalmemory/storage/schema.py` | Modify | ~60 | 8 个表增加 `scope TEXT DEFAULT 'personal'` + `shared_with TEXT` + 复合索引 |
| `src/superlocalmemory/storage/models.py` | Modify | ~30 | 15+ dataclass 增加 `scope` / `shared_with` 字段 |
| `src/superlocalmemory/storage/migrations/M016_add_scope_support.py` | **Create** | ~55 | 迁移脚本，向后兼容（现有数据默认 `personal`） |
| `src/superlocalmemory/storage/migration_runner.py` | Modify | ~5 | 注册 M016 到迁移列表 |
| `src/superlocalmemory/storage/migrations/__init__.py` | Modify | ~2 | 导出 M016 模块 |
| `tests/test_cli_scope.py` | **Create** | ~50 | scope 列存在性和默认值测试 |

### 排除项

- `database.py` — **不修改**。现有 INSERT 语句无需改动（列有 DEFAULT）
- `entity_resolver.py` — **不修改**。Phase 2B 的 `_get_global_entity()` 不属于此 PR
- `M015_add_domain_tags.py` — **不包含**。域标签是 Phase 2B

### 8 个核心表

```
memories           → scope, shared_with
atomic_facts       → scope, shared_with
canonical_entities → scope, shared_with
temporal_events    → scope, shared_with
graph_edges        → scope, shared_with
memory_edges       → scope, shared_with
kg_nodes           → scope, shared_with
session_events     → scope, shared_with
```

每个表增加两个索引：
```sql
CREATE INDEX idx_<table>_scope ON <table> (scope);
CREATE INDEX idx_<table>_profile_scope ON <table> (profile_id, scope);
```

### 实施步骤

#### Task 1.1: 创建隔离 worktree

- [ ] **Step 1: 确保 upstream/main 是最新的**

```bash
cd /home/kai-remote/github/superlocalmemory
git fetch upstream
git log upstream/main --oneline -3
```

- [ ] **Step 2: 创建 PR-A worktree**

```bash
git worktree add /tmp/pr-a-schema upstream/main
cd /tmp/pr-a-schema
git checkout -b pr/scope-schema-migration
```

#### Task 1.2: 应用 Schema 变更

- [ ] **Step 3: 修改 schema.py — 仅添加 scope/shared_with 列和索引**

从当前 main 分支提取 schema.py 中的 scope 相关变更。注意：**不要包含** M015 (domain_tags) 的列，**不修改**任何已有列定义。

具体变更（8 个表，每个表增加 2 列 + 2 个索引）：

```sql
-- 每个表增加:
scope        TEXT NOT NULL DEFAULT 'personal',
shared_with  TEXT,

-- 每个表增加索引:
CREATE INDEX IF NOT EXISTS idx_<table>_scope ON <table> (scope);
CREATE INDEX IF NOT EXISTS idx_<table>_profile_scope ON <table> (profile_id, scope);
```

- [ ] **Step 4: 修改 models.py — 仅添加 scope/shared_with 字段**

为所有相关 dataclass 添加：
```python
scope: str = "personal"
shared_with: list[str] | None = None
```

- [ ] **Step 5: 创建 M016_add_scope_support.py 迁移文件**

```python
NAME = "M016_add_scope_support"

DDL = """
ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'personal';
ALTER TABLE memories ADD COLUMN shared_with TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories (scope);
CREATE INDEX IF NOT EXISTS idx_memories_profile_scope ON memories (profile_id, scope);
-- ... (8 个表同上)
"""
```

- [ ] **Step 6: 注册 M016 到 migration_runner.py**

在 `DEFERRED_MIGRATIONS` 列表末尾添加 M016，在 imports 中添加 `M016_add_scope_support as _M016`。

- [ ] **Step 7: 更新 migrations/__init__.py**

添加 `from . import M016_add_scope_support`

#### Task 1.3: 测试

- [ ] **Step 8: 创建基础 scope schema 测试**

```python
# tests/test_cli_scope.py
def test_scope_column_exists_on_memories(in_memory_db):
    """验证 memories 表有 scope 列且默认值为 personal"""
    db = in_memory_db
    db.execute("INSERT INTO memories (memory_id, content, profile_id) VALUES (?, ?, ?)",
               ["test-1", "test content", "default"])
    row = db.execute("SELECT scope, shared_with FROM memories WHERE memory_id = ?", 
                     ["test-1"]).fetchone()
    assert row["scope"] == "personal"
    assert row["shared_with"] is None

def test_migration_m016_is_registered():
    """验证 M016 在迁移列表中"""
    from superlocalmemory.storage.migration_runner import DEFERRED_MIGRATIONS
    names = [m.name for m in DEFERRED_MIGRATIONS]
    assert "M016_add_scope_support" in names
```

- [ ] **Step 9: 运行测试确保仅 schema 变更通过**

```bash
pytest tests/test_cli_scope.py -v
pytest tests/ -x -q --tb=short  # 确保不影响现有测试
```

预期：新增测试 PASS，现有全部测试 PASS。

#### Task 1.4: 提交并验证

- [ ] **Step 10: 验证 diff 干净（仅包含预期的 5 个文件）**

```bash
git diff upstream/main --stat
# 应只显示:
# src/superlocalmemory/storage/schema.py
# src/superlocalmemory/storage/models.py
# src/superlocalmemory/storage/migrations/M016_add_scope_support.py
# src/superlocalmemory/storage/migration_runner.py
# src/superlocalmemory/storage/migrations/__init__.py
# tests/test_cli_scope.py
```

- [ ] **Step 11: 提交**

```bash
git add -A
git commit -m "feat: add scope and shared_with columns for multi-scope memory (schema only)

Add scope (TEXT, default 'personal') and shared_with (TEXT, nullable)
columns to 8 core tables, plus composite indexes on (scope) and
(profile_id, scope). Existing data defaults to 'personal' for backward
compatibility.

Migration M016 handles ALTER TABLE for existing databases.

This is PR 1/3 of the multi-scope memory feature — storage layer only.
No retrieval or interface changes included.

Ref: #20"
```

---

## Chunk 2: PR-B — Retrieval Layer（~1,000 LOC）

### 目标

在 PR-A 的 schema 基础上，使 7 个检索通道支持 scope 过滤，实现多 scope 并行检索 + 加权 RRF 融合。**不包含任何 MCP/CLI 接口变更**——所有新参数有默认值，保持现有 API 兼容。

### 文件清单

| 文件 | 操作 | LOC | 说明 |
|------|------|-----|------|
| `src/superlocalmemory/storage/database.py` | Modify | ~600 | `_scope_where()` 辅助函数 + 30+ 查询方法增加可选 scope 参数 |
| `src/superlocalmemory/core/recall_pipeline.py` | Modify | ~100 | 多 scope 并行检索 + 加权 RRF 融合 |
| `src/superlocalmemory/retrieval/bm25_channel.py` | Modify | ~30 | scope 过滤 |
| `src/superlocalmemory/retrieval/semantic_channel.py` | Modify | ~30 | scope 过滤 |
| `src/superlocalmemory/retrieval/entity_channel.py` | Modify | ~20 | scope 过滤 |
| `src/superlocalmemory/retrieval/temporal_channel.py` | Modify | ~20 | scope 过滤 |
| `src/superlocalmemory/retrieval/hopfield_channel.py` | Modify | ~20 | scope 过滤 |
| `src/superlocalmemory/retrieval/profile_channel.py` | Modify | ~20 | scope 过滤 |
| `src/superlocalmemory/retrieval/spreading_activation.py` | Modify | ~20 | scope 过滤 |
| `src/superlocalmemory/retrieval/engine.py` | Modify | ~10 | scope 参数传递 |
| `src/superlocalmemory/core/config.py` | Modify | ~30 | `ScopeWeights` 配置类 |
| `tests/test_core/test_recall_pipeline.py` | Create | ~80 | 多 scope 检索测试 |

### 排除项

- `engine.py` — **不修改**（接口变更在 PR-C）
- `mcp/tools_core.py` — **不修改**（接口变更在 PR-C）
- `cli/commands.py` — **不修改**（接口变更在 PR-C）
- `entity_resolver.py` 中的 `_get_global_entity()` — **不包含**（Phase 2B）

### 关键设计：database.py 变更的向后兼容性

所有查询方法的新 scope 参数都有默认值，确保现有调用者不受影响：

```python
def get_facts_by_entity(
    self, entity_id: str, profile_id: str,
    scope: str = "personal",           # NEW, with default
    include_global: bool = True,       # NEW, with default
    include_shared: bool = True,       # NEW, with default
) -> list[AtomicFact]:
```

### 关键设计：_scope_where() 辅助函数

```python
@staticmethod
def _scope_where(
    profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    prefix: str = "",
    skill_tags: list[str] | None = None,
) -> tuple[str, list]:
    """构建 scope 过滤的 WHERE 子句。
    
    默认值（include_global=True, include_shared=True）保持向后兼容——
    与现有单 profile 行为一致。
    """
```

### 实施步骤

#### Task 2.1: 创建隔离 worktree

- [ ] **Step 1: 从 PR-A 分支创建 PR-B worktree**

```bash
git worktree add /tmp/pr-b-retrieval pr/scope-schema-migration
cd /tmp/pr-b-retrieval
git checkout -b pr/scope-retrieval
```

#### Task 2.2: 应用 database.py 变更

- [ ] **Step 2: 添加 _scope_where() 静态方法**
- [ ] **Step 3: 为 30+ 查询方法增加 scope/include_global/include_shared 参数**

变更模式（每个方法相同）：
```python
# Before:
def get_facts_by_entity(self, entity_id: str, profile_id: str) -> list[AtomicFact]:
    sql = "SELECT * FROM atomic_facts WHERE entity_id = ? AND profile_id = ?"

# After:
def get_facts_by_entity(
    self, entity_id: str, profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
) -> list[AtomicFact]:
    where_clause, params = self._scope_where(
        profile_id, scope, include_global, include_shared
    )
    sql = f"SELECT * FROM atomic_facts WHERE entity_id = ? AND {where_clause}"
```

- [ ] **Step 4: 更新 INSERT/UPDATE 方法以存储 scope/shared_with**

在 `store_fact()`, `store_memory()` 等方法中传递 `scope` 和 `shared_with`。

#### Task 2.3: 应用 Retrieval Channel 变更

- [ ] **Step 5: 为 7 个 channel 的 search() 方法增加 scope 参数**

每个 channel 的模式：
```python
# Before:
def search(self, query: str, profile_id: str, **kwargs) -> list[SearchResult]:

# After:
def search(self, query: str, profile_id: str, *,
           scope: str = "personal",
           include_global: bool = True,
           include_shared: bool = True,
           **kwargs) -> list[SearchResult]:
```

- [ ] **Step 6: 更新 channel 内部查询以使用 scope 参数**

#### Task 2.4: 应用 recall_pipeline 变更

- [ ] **Step 7: 实现多 scope 并行检索**

```python
def recall_pipeline(
    query: str,
    profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    ...
) -> RecallResponse:
    # 并行检索 personal + global + shared
    # RRF 加权融合
```

- [ ] **Step 8: 添加 ScopeWeights 配置类到 config.py**

```python
@dataclass
class ScopeWeights:
    personal: float = 1.0
    shared: float = 0.7
    global: float = 0.5
```

#### Task 2.5: 测试

- [ ] **Step 9: 编写多 scope 检索测试**

```python
def test_recall_personal_scope_only(in_memory_db):
    """验证 personal scope 只返回当前 profile 的记忆"""

def test_recall_includes_global_scope(in_memory_db):
    """验证 include_global=True 时返回全局记忆"""

def test_recall_scope_weighted_fusion(in_memory_db):
    """验证 RRF 融合按权重排序"""
```

- [ ] **Step 10: 运行测试**

```bash
pytest tests/test_core/test_recall_pipeline.py -v
pytest tests/ -x -q --tb=short
```

预期：新增测试 PASS，现有全部测试 PASS。

#### Task 2.6: 验证并提交

- [ ] **Step 11: 验证 diff 干净**

```bash
git diff pr/scope-schema-migration --stat
# 应只包含 retrieval 相关文件，不包含 engine.py / mcp/ / cli/
```

- [ ] **Step 12: 提交**

```bash
git add -A
git commit -m "feat: scope-aware retrieval with weighted RRF fusion

Add scope filtering to all 7 retrieval channels and implement
multi-scope parallel retrieval with configurable RRF fusion weights.

- database.py: _scope_where() helper + scope params on 30+ query methods
- recall_pipeline: parallel personal/global/shared retrieval + weighted RRF
- 7 channels: scope-aware filtering in each channel
- config.py: ScopeWeights (personal=1.0, shared=0.7, global=0.5)

All new parameters have backward-compatible defaults.
Requires PR #<PR-A-number> (schema + migration).

This is PR 2/3 of the multi-scope memory feature.

Ref: #20"
```

---

## Chunk 3: PR-C — External Interface（~400 LOC）

### 目标

在 PR-A（Schema）和 PR-B（Retrieval）基础上，暴露 scope 控制给 MCP 工具、CLI 命令、和 Python API。所有参数有默认值，保持向后兼容。

### 文件清单

| 文件 | 操作 | LOC | 说明 |
|------|------|-----|------|
| `src/superlocalmemory/core/engine.py` | Modify | ~40 | `store()` 和 `recall()` 签名扩展 |
| `src/superlocalmemory/mcp/tools_core.py` | Modify | ~50 | `remember` scope/shared_with；`recall` include_global/include_shared |
| `src/superlocalmemory/mcp/tools_context.py` | Modify | ~20 | `session_init` 和 `observe` scope 参数传递 |
| `src/superlocalmemory/mcp/tools_active.py` | Modify | ~10 | 活跃工具 scope 支持 |
| `src/superlocalmemory/cli/commands.py` | Modify | ~80 | `remember --scope`；`recall --include-global`；`entity list --scope` |
| `src/superlocalmemory/core/store_pipeline.py` | Modify | ~30 | scope/shared_with 参数透传 |
| `src/superlocalmemory/core/__init__.py` | Modify | ~5 | 导出 ScopeWeights（如需要） |
| `tests/test_cli_subparsers/test_remember_scope.py` | Create | ~60 | CLI scope 参数测试 |
| `tests/test_mcp_scope.py` | Create | ~80 | MCP scope 工具测试 |

### 排除项

- `entity list --scope shared` 的 shared scope 过滤 — Phase 2B（当前 scope 参数接受但不支持 shared 粒度过滤）
- `slm_report_feedback` MCP 工具 — 属于 MSLM 发行版功能

### 关键变更示例

**engine.py:**
```python
def store(self, content: str, *,
          profile_id: str | None = None,
          scope: str = "personal",           # NEW
          shared_with: list[str] | None = None,  # NEW
          **kwargs) -> list[str]:

def recall(self, query: str, *,
           profile_id: str | None = None,
           include_global: bool = True,      # NEW
           include_shared: bool = True,      # NEW
           **kwargs) -> RecallResponse:
```

**MCP remember 工具:**
```python
async def remember(
    content: str,
    scope: str = "personal",       # NEW
    shared_with: str = "",         # NEW (comma-separated)
    ...
)
```

**CLI:**
```bash
slm remember "content" --scope global
slm remember "content" --scope shared --shared-with "agent1,agent2"
slm recall "query" --include-global --include-shared
slm entity list --scope personal|global|shared
```

### 实施步骤

#### Task 3.1: 创建隔离 worktree

- [ ] **Step 1: 从 PR-B 分支创建 PR-C worktree**

```bash
git worktree add /tmp/pr-c-interface pr/scope-retrieval
cd /tmp/pr-c-interface
git checkout -b pr/scope-interface
```

#### Task 3.2: 应用 Engine 变更

- [ ] **Step 2: 扩展 engine.store() 签名**
- [ ] **Step 3: 扩展 engine.recall() 签名**

#### Task 3.3: 应用 MCP 工具变更

- [ ] **Step 4: 扩展 remember 工具（scope + shared_with）**
- [ ] **Step 5: 扩展 recall 工具（include_global + include_shared）**
- [ ] **Step 6: 扩展 entity list 工具（--scope）**

#### Task 3.4: 应用 CLI 变更

- [ ] **Step 7: 扩展 remember 命令**
- [ ] **Step 8: 扩展 recall 命令**
- [ ] **Step 9: 扩展 entity list/merge 命令**

#### Task 3.5: 应用 store_pipeline 变更

- [ ] **Step 10: scope/shared_with 参数透传**

#### Task 3.6: 测试

- [ ] **Step 11: 编写 CLI scope 参数测试**
- [ ] **Step 12: 编写 MCP scope 工具测试**
- [ ] **Step 13: 运行全部测试**

```bash
pytest tests/test_cli_subparsers/test_remember_scope.py -v
pytest tests/test_mcp_scope.py -v
pytest tests/ -x -q --tb=short
```

#### Task 3.7: 验证并提交

- [ ] **Step 14: 验证 diff 干净**

```bash
git diff pr/scope-retrieval --stat
# 确认不包含任何 retrieval 层或 schema 层文件
```

- [ ] **Step 15: 提交**

```bash
git add -A
git commit -m "feat: expose multi-scope memory controls via MCP, CLI, and Python API

Add scope/shared_with parameters to engine.store(), engine.recall(),
MCP remember/recall tools, and CLI commands.

- engine.py: store() scope/shared_with; recall() include_global/include_shared
- MCP tools: remember gets scope + shared_with; recall gets scope flags
- CLI: slm remember --scope; slm recall --include-global; slm entity list --scope
- store_pipeline: scope/shared_with passthrough

All new parameters have backward-compatible defaults (scope='personal').
Requires PR #<PR-B-number> (retrieval layer).

This is PR 3/3 of the multi-scope memory feature.

Ref: #20"
```

---

## Chunk 4: 最终验证与清理

### Task 4.1: 端到端验证

- [ ] **Step 1: 验证 PR-C 分支包含全部三层变更**

```bash
cd /tmp/pr-c-interface
git diff upstream/main --stat
# 确认只包含预期的 ~15-20 个文件
```

- [ ] **Step 2: 在 PR-C 分支运行全量测试**

```bash
pytest tests/ -x -q --tb=short
```

- [ ] **Step 3: 验证每个中间分支也可独立通过测试**

```bash
cd /tmp/pr-a-schema && pytest tests/ -x -q --tb=short
cd /tmp/pr-b-retrieval && pytest tests/ -x -q --tb=short
```

### Task 4.2: 清理

- [ ] **Step 4: 删除 worktrees**

```bash
git worktree remove /tmp/pr-a-schema
git worktree remove /tmp/pr-b-retrieval
git worktree remove /tmp/pr-c-interface
```

---

## 三层 PR 依赖链

```
upstream/main
    │
    └── pr/scope-schema-migration (PR-A)
            │
            └── pr/scope-retrieval (PR-B)
                    │
                    └── pr/scope-interface (PR-C)
```

## 文件归属总结

| 文件 | PR-A | PR-B | PR-C | Phase 2B |
|------|:----:|:----:|:----:|:--------:|
| `storage/schema.py` | ✅ | | | |
| `storage/models.py` | ✅ | | | |
| `storage/migrations/M016_*.py` | ✅ | | | |
| `storage/migration_runner.py` | ✅ | | | |
| `storage/database.py` | | ✅ | | |
| `retrieval/engine.py` | | ✅ | | |
| `retrieval/bm25_channel.py` | | ✅ | | |
| `retrieval/semantic_channel.py` | | ✅ | | |
| `retrieval/entity_channel.py` | | ✅ | | |
| `retrieval/temporal_channel.py` | | ✅ | | |
| `retrieval/hopfield_channel.py` | | ✅ | | |
| `retrieval/profile_channel.py` | | ✅ | | |
| `retrieval/spreading_activation.py` | | ✅ | | |
| `core/recall_pipeline.py` | | ✅ | | |
| `core/config.py` (ScopeWeights) | | ✅ | | |
| `core/engine.py` | | | ✅ | |
| `core/store_pipeline.py` | | | ✅ | |
| `mcp/tools_core.py` | | | ✅ | |
| `mcp/tools_context.py` | | | ✅ | |
| `mcp/tools_active.py` | | | ✅ | |
| `cli/commands.py` | | | ✅ | |
| `encoding/entity_resolver.py` | | | | ✅ |
| `storage/seed_domain_mapping.py` | | | | ✅ |
| `storage/migrations/M015_*.py` | | | | ✅ |

---

## 注意事项

1. **不提交 MSLM 品牌文件**：README-zh.md, README-en.md, docs/ 中的品牌层文件、package.json 等属于 MSLM 发行版，不在任何上游 PR 中
2. **不提交 Phase 2B**：`_get_global_entity()`、域标签、`seed_domain_mapping.py` 等待设计对齐后单独 PR
3. **不提交 M015**：域标签迁移脚本是 Phase 2B，注意 `migration_runner.py` 中 M015 引用要从当前 fork 的 `M015_add_domain_tags` 恢复为上游的 `M015_add_pinned_column`
4. **每个 PR 独立可测**：PR-A 只加列不改查询 → 现有测试全通过。PR-B 加查询逻辑 → 新测试 + 现有测试全通过。PR-C 加接口 → 端到端测试通过
5. **提交信息引用 #20**：每个 PR 的 commit message 和 PR description 中引用上游 Issue #20
