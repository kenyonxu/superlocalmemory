# 多层次结构记忆实现路径分析
> SuperLocalMemory V3 Architecture Design Document
> Author: Claude Code Analysis | Date: 2026-04-24

本文档分析在 SLM V3 基础上实现**多层级记忆隔离与共享**的架构改动方案，目标支持：

1. **Profile 级独立记忆** — 每个用户拥有独立记忆空间（已有）
2. **群组记忆（Group Scope）** — 可自定义编组，组内成员共享
3. **全局记忆（Global Scope）** — 全员共享的公共知识库

---

## 1. 当前架构现状

### 1.1 单维度隔离模型

SLM 当前以 `profile_id` 为唯一隔离边界，贯穿存储层到 MCP 接口：

```
┌─────────────────────────────────────────────┐
│  MCP Tools                                  │
│  remember(profile_id="alice")               │
│  recall(profile_id="alice")                 │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│  MemoryEngine                               │
│  _profile_id = "alice"                      │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│  StorePipeline / RecallPipeline             │
│  profile_id 透传                            │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│  DatabaseManager                            │
│  WHERE profile_id = ?                       │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│  SQLite Schema                              │
│  profile_id TEXT NOT NULL DEFAULT 'default' │
└─────────────────────────────────────────────┘
```

### 1.2 profile_id 渗透范围

| 层级 | 文件 | 涉及代码量 | profile_id 使用方式 |
|------|------|-----------|-------------------|
| Schema | `storage/schema.py` | 8 个核心表 | 列定义 + 外键 + 索引 |
| Models | `storage/models.py` | 15+ 数据类 | 字段默认值 |
| DB 查询 | `storage/database.py` | 30+ 方法 | SQL WHERE 条件 |
| 存储管线 | `core/store_pipeline.py` | 全链路 | 参数透传 |
| 检索管线 | `core/recall_pipeline.py` | 全链路 | 参数透传 + 信号收集 |
| 引擎 | `core/engine.py` | 核心方法 | 实例属性 |
| 检索通道 | `retrieval/*.py` | 7 个 channel | profile 过滤 |
| MCP 工具 | `mcp/tools_*.py` | 20+ 工具 | 参数 + 回退逻辑 |

---

## 2. 目标架构设计

### 2.1 三作用域模型

引入独立的 `scope` 维度，`profile_id` 保持原意：

```
┌────────────────────────────────────────────────────────────┐
│                    Scope 维度                              │
├─────────────┬─────────────┬────────────────────────────────┤
│  personal   │   group     │          global                │
│  (个人)     │   (群组)    │         (全局)                 │
├─────────────┼─────────────┼────────────────────────────────┤
│ profile_id  │ group_id    │  无 profile/group 限制         │
│ 隔离        │ + scope     │  全员只读共享                  │
└─────────────┴─────────────┴────────────────────────────────┘
```

**设计原则**：
- `profile_id` = 用户画像（技术栈、偏好、工作习惯）
- `scope` = 可见性边界（个人 / 群组 / 全局）
- `group_id` = 群组标识（仅 `scope='group'` 时有效）

### 2.2 检索时 Scope 合并策略

用户 `alice` 在 `backend` 组中时的 recall 数据流：

```
query ──┬──► personal scope (profile_id="alice")
        │    └── 仅返回 alice 的个人记忆
        │
        ├──► group scope (group_id="backend")
        │    └── 返回 backend 组的所有成员贡献的记忆
        │
        └──► global scope
             └── 返回全员共享的全局记忆

              ↓
        [RRF 融合] ──► 跨 scope 排序 ──► 返回 Top-K
```

**关键决策**：各 scope 在 RRF 中的权重如何分配？建议默认策略：
- `personal` 权重 1.0（最优先）
- `group` 权重 0.8
- `global` 权重 0.6

---

## 3. 改动方案

### 3.1 Schema 层（~150 行）

**文件**: `storage/schema.py`

在 8 个核心表上新增列：

```sql
-- atomic_facts 示例
CREATE TABLE atomic_facts (
    fact_id          TEXT PRIMARY KEY,
    profile_id       TEXT NOT NULL DEFAULT 'default',
    scope            TEXT NOT NULL DEFAULT 'personal',  -- NEW
    group_id         TEXT,                               -- NEW (nullable)
    content          TEXT NOT NULL,
    ...
);

-- 新增复合索引
CREATE INDEX idx_facts_scope ON atomic_facts(scope, group_id);
CREATE INDEX idx_facts_profile_scope ON atomic_facts(profile_id, scope);
```

**影响表清单**：
- `atomic_facts`
- `memories`
- `canonical_entities`
- `kg_nodes`
- `graph_edges`
- `memory_edges`
- `temporal_events`
- `audit_trail`

**连带工作**：
- 创建迁移脚本 `storage/migrations/M0xx_add_scope_support.py`
- 更新 `migration_runner.py` 注册
- 更新 `models.py` 15+ 数据类新增字段

---

### 3.2 DatabaseManager 层（~200 行）

**文件**: `storage/database.py`

当前查询签名（30+ 个方法）：

```python
def search_facts_fts(self, query: str, profile_id: str, limit: int = 20)
def get_facts_by_entity(self, entity_id: str, profile_id: str)
def get_facts_by_type(self, fact_type: FactType, profile_id: str)
# ...
```

目标签名：

```python
def search_facts_fts(
    self, query: str, profile_id: str,
    scopes: list[str] | None = None,
    group_ids: list[str] | None = None,
    limit: int = 20
)
```

SQL 改造示例：

```python
# 改造前
sql = "SELECT * FROM atomic_facts WHERE profile_id = ? AND ..."

# 改造后
sql = """
    SELECT * FROM atomic_facts
    WHERE (
        (scope = 'personal' AND profile_id = ?)
        OR (scope = 'global')
        OR (scope = 'group' AND group_id IN ({}))
    )
    AND ...
""".format(','.join('?' * len(group_ids)))
```

---

### 3.3 MemoryEngine 层（~100 行）

**文件**: `core/engine.py`

存储和检索签名扩展：

```python
def store(
    self, content: str,
    profile_id: str | None = None,
    scope: str = "personal",
    group_id: str | None = None,
) -> StoreResult

def recall(
    self, query: str,
    profile_id: str | None = None,
    scopes: list[str] | None = None,
    group_ids: list[str] | None = None,
    ...
) -> RecallResult
```

引擎内部维护的状态从单一 `profile_id` 扩展为三元组：

```python
# 当前
self._profile_id: str = "default"

# 目标
self._context: MemoryContext = MemoryContext(
    profile_id="default",
    default_scope="personal",
    group_memberships=["backend", "frontend"],
)
```

---

### 3.4 Store Pipeline（~80 行）

**文件**: `core/store_pipeline.py`

改动相对简单：将 `scope` / `group_id` 透传到 DB 层。

```python
def store_memory(
    content: str,
    profile_id: str,
    scope: str = "personal",           # 新增
    group_id: str | None = None,       # 新增
    ...
) -> StoreResult:
    # ... 现有逻辑 ...
    db.insert_fact(
        profile_id=profile_id,
        scope=scope,                    # 新增
        group_id=group_id,              # 新增
        ...
    )
```

---

### 3.5 Recall Pipeline（~250 行）

**文件**: `core/recall_pipeline.py`

**这是改动最大的部分。**

当前流程：
```
query → 7 channels（单 profile）→ RRF 融合 → 返回
```

目标流程：
```
query ──┬──► personal scope 检索（profile_id）
        ├──► group scope 检索（每个 member group）
        ├──► global scope 检索（无 profile 限制）
        │
        └──► 合并三组结果 ──► RRF 融合（含 scope 权重）──► 返回
```

实现策略：

```python
def recall_pipeline(
    query: str,
    profile_id: str,
    scopes: list[str] | None = None,
    group_ids: list[str] | None = None,
    ...
) -> RecallResponse:
    scopes = scopes or ["personal", "group", "global"]
    group_ids = group_ids or get_user_groups(profile_id)

    all_results: dict[str, list[SearchResult]] = {}

    # 1. Personal scope
    if "personal" in scopes:
        all_results["personal"] = _run_channels(query, profile_id, scope="personal")

    # 2. Group scope
    if "group" in scopes:
        for gid in group_ids:
            all_results[f"group:{gid}"] = _run_channels(query, profile_id, scope="group", group_id=gid)

    # 3. Global scope
    if "global" in scopes:
        all_results["global"] = _run_channels(query, profile_id=None, scope="global")

    # 4. 带权 RRF 融合
    return _weighted_rrf_fusion(all_results, scope_weights={
        "personal": 1.0,
        "group": 0.8,
        "global": 0.6,
    })
```

---

### 3.6 7 个检索 Channel（~150 行）

**文件**: `retrieval/*.py`

每个 channel 增加 `scope` / `group_id` 过滤：

| Channel | 核心改动 |
|---------|---------|
| `semantic_channel.py` | 向量查询增加 scope 过滤条件 |
| `bm25_channel.py` | FTS 查询增加 scope 过滤 |
| `entity_channel.py` | 图遍历增加 scope 边界检查 |
| `temporal_channel.py` | 时间查询增加 scope 条件 |
| `hopfield_channel.py` | 联想检索增加 scope 边界 |
| `profile_channel.py` | 实体画像扩展 group/global 画像 |
| `spreading_activation.py` | 图传播增加 scope 限制 |

---

### 3.7 MCP 工具层（~120 行）

**文件**: `mcp/tools_core.py` 及相关

现有工具签名扩展：

```python
# remember
async def remember(
    content: str, tags: str = "",
    profile_id: str = "",
    scope: str = "personal",       # 新增
    group_id: str = "",            # 新增
) -> dict

# recall
async def recall(
    query: str, limit: int = 10,
    profile_id: str = "",
    include_global: bool = True,   # 新增
    include_groups: bool = True,   # 新增
) -> dict
```

**新增工具**（4 个）：

```python
async def join_group(group_id: str, profile_id: str = "") -> dict
async def leave_group(group_id: str, profile_id: str = "") -> dict
async def list_groups(profile_id: str = "") -> dict
async def create_group(group_id: str, name: str, description: str = "") -> dict
```

---

### 3.8 配置与 Profile 管理（~50 行）

**文件**: `core/config.py`, `core/profiles.py`

- `SLMConfig` 增加 `default_scope`、`group_memberships` 配置项
- `profiles` 表新增群组关联子表：

```sql
CREATE TABLE profile_groups (
    profile_id TEXT NOT NULL,
    group_id   TEXT NOT NULL,
    joined_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_id, group_id),
    FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
);
```

---

### 3.9 知识图谱跨 Scope 一致性（设计级）

**风险等级：高 | 设计复杂度：高**

当前图谱实体和边全部是 `profile_id` 隔离的。引入多 scope 后需决策：

**实体命名空间策略**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A. 独立命名空间 | 每个 scope 有独立实体 ID | 实现简单 | 同一实体在不同 scope 有重复 ID |
| B. 全局权威源 | global 实体为权威，其他 scope 引用 | 一致性好 | 需要跨 scope 消歧逻辑 |
| C. 混合方案 | global 实体统一，personal 可创建本地别名 | 平衡 | 实现复杂 |

**推荐方案 C**：
- global scope 的实体是权威定义
- personal scope 可通过 `entity_alias` 表引用 global 实体或创建本地覆盖
- group scope 实体默认继承 global，允许组内覆盖

```sql
CREATE TABLE entity_aliases (
    alias_id      TEXT PRIMARY KEY,
    profile_id    TEXT,
    group_id      TEXT,
    canonical_name TEXT NOT NULL,
    references_global_entity_id TEXT,  -- NULL = 本地定义
    scope         TEXT NOT NULL DEFAULT 'personal'
);
```

---

## 4. 改动量汇总

| 层级 | 文件 | 代码行数 | 改动类型 |
|------|------|---------|---------|
| Schema | `storage/schema.py` | ~150 | 新增列 + 索引 |
| Models | `storage/models.py` | ~50 | 新增字段 |
| DB 查询 | `storage/database.py` | ~200 | 方法签名 + SQL |
| 引擎 | `core/engine.py` | ~100 | 方法签名 + 状态管理 |
| 存储管线 | `core/store_pipeline.py` | ~80 | 参数透传 |
| 检索管线 | `core/recall_pipeline.py` | ~250 | 多 scope 检索 + 融合 |
| 检索通道 | `retrieval/*.py` (7个) | ~150 | 过滤条件 |
| MCP 工具 | `mcp/tools_*.py` | ~120 | 参数扩展 + 新工具 |
| 配置 | `core/config.py`, `profiles.py` | ~50 | 新配置项 + 关联表 |
| 迁移 | `storage/migrations/` | ~50 | 迁移脚本 |
| **合计** | **~15 个文件** | **~1200-1500** | **架构级升级** |

---

## 5. 实施路线图

### Phase 1: 存储层（1 周）

- [ ] Schema 新增 `scope` / `group_id` 列（8 个表）
- [ ] 写数据库迁移脚本
- [ ] 重构 `DatabaseManager` 30+ 查询方法
- [ ] 更新 `models.py` 数据类
- [ ] 单元测试覆盖

### Phase 2: 引擎与 Pipeline（1 周）

- [ ] `MemoryEngine` 支持 `scope` / `group_id` 参数
- [ ] `store_pipeline` 透传 scope
- [ ] `recall_pipeline` 实现多 scope 并行检索
- [ ] 设计并实现带权 RRF 融合策略
- [ ] 7 个检索 channel 增加 scope 过滤
- [ ] 集成测试覆盖

### Phase 3: MCP 与配置层（0.5 周）

- [ ] MCP tools 扩展参数
- [ ] 新增 group 管理工具（join/leave/list/create）
- [ ] `SLMConfig` 增加 scope 配置
- [ ] `ProfileManager` 增加群组关联
- [ ] 端到端测试

### Phase 4: 知识图谱一致性（1 周，可选）

- [ ] 设计实体命名空间策略
- [ ] 实现 `entity_aliases` 表
- [ ] 跨 scope 实体消歧逻辑
- [ ] Sheaf 一致性检查的 scope 边界处理

---

## 6. 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 数据迁移兼容性 | 🟡 中 | 迁移脚本将现有数据 `scope` 设为 `personal`，保持向后兼容 |
| 检索性能回归 | 🟡 中 | 新增 `scope` + `group_id` 复合索引；多 scope 查询可并行执行 |
| 隐私边界泄漏 | 🔴 高 | 所有 DB 查询必须通过 scope 过滤 enforce；集成测试覆盖越权访问场景 |
| 知识图谱一致性 | 🔴 高 | 采用混合命名空间方案；global 实体为权威源 |
| MCP 工具向后兼容 | 🟡 中 | 新增参数设默认值 `personal`，现有调用无需修改 |

---

## 7. 关键设计决策待确认

1. **RRF 融合权重**：personal / group / global 的默认权重比例？
2. **Group 权限模型**：是否支持 group 管理员？谁可以创建/删除 group？
3. **Global 记忆可写性**：global scope 是只读还是允许特定角色写入？
4. **实体消歧策略**：同名实体在不同 scope 中如何处理？
5. **数据保留策略**：group 解散后记忆如何处理？迁移到 personal 还是删除？

---

## 附录：当前架构 profile_id 全链路追踪

```
MCP Tool 调用
    │ tools_core.py:67  pid = engine.profile_id
    │ tools_core.py:229 profile_id="" 回退到 engine.profile_id
    │
    ▼
MemoryEngine
    │ engine.py:58   self._profile_id = config.active_profile
    │ engine.py:375  recall(query, profile_id=None)
    │ engine.py:392  pid = profile_id or self._profile_id
    │ engine.py:330  store(content, self._profile_id)
    │
    ▼
StorePipeline
    │ store_pipeline.py:55   profile_id 参数
    │ store_pipeline.py:87   db.insert_fact(profile_id=profile_id)
    │ store_pipeline.py:111  profile_id 参数
    │ store_pipeline.py:146  "profile_id": profile_id
    │
    ▼
RecallPipeline
    │ recall_pipeline.py:120   profile_id 参数
    │ recall_pipeline.py:140   profile_id=profile_id
    │ recall_pipeline.py:209   profile_id 参数
    │ recall_pipeline.py:231   apply_adaptive_ranking(..., profile_id)
    │ recall_pipeline.py:384   "profile_id": profile_id
    │ recall_pipeline.py:575   retrieval_engine.recall(query, profile_id)
    │
    ▼
Retrieval Channels
    │ profile_channel.py:60    search(query, profile_id)
    │ semantic_channel.py      profile_id 过滤
    │ bm25_channel.py          profile_id 过滤
    │ entity_channel.py        profile_id 过滤
    │ temporal_channel.py      profile_id 过滤
    │ hopfield_channel.py      profile_id 过滤
    │ spreading_activation.py  profile_id 过滤
    │
    ▼
DatabaseManager
    │ database.py:255  get_facts_by_entity(entity_id, profile_id)
    │ database.py:270  get_facts_by_type(fact_type, profile_id)
    │ database.py:470  search_facts_fts(query, profile_id)
    │ database.py:1067 get_facts_needing_decay(profile_id)
    │
    ▼
SQLite Schema (8 个核心表)
    │ schema.py:112    memories.profile_id
    │ schema.py:142    atomic_facts.profile_id
    │ schema.py:192    graph_edges.profile_id
    │ schema.py:289    canonical_entities.profile_id
    │ schema.py:340    temporal_events.profile_id
```
