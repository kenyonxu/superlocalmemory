# Spec: 三层作用域端到端传递

> 2026-05-15 | SuperLocalMemory V3.4.45+

## 问题

SLM 支持三层记忆作用域（personal / global / shared），但 scope 参数在多条异步路径中丢失，只有 Python API 的同步 `engine.store()` 能正确写入 `atomic_facts.scope`。

### 当前状态

| 入口 | scope 参数 | 到达 atomic_facts |
|------|-----------|------------------|
| Python API `engine.store()` | 有 | 正确 |
| MCP `remember` | 有，写入 metadata JSON | **丢失**（材质化线程不提取） |
| CLI `slm remember` | **无参数** | **丢失** |
| Dashboard `/api/import` | **无参数** | **丢失** |

### 根因

材质化线程（`unified_daemon.py` 的 `_loop()`）是所有异步 remember 的汇聚点。它从 `pending_memories.metadata` JSON 读出元数据后：

1. 创建 `AtomicFact` 时未设置 `scope`（默认 `"personal"`）
2. `INSERT INTO memories` 时未包含 `scope` 列
3. `shared_with` 信息完全丢弃

即使入口正确传入 scope，只要走异步路径（默认行为），scope 就会被材质化线程吞掉。

## 方案

从 metadata JSON 中提取 scope/shared_with，在材质化线程中设置到 `AtomicFact` 和 `memories` INSERT。同时为 CLI 和 Dashboard 入口补上 scope 参数传递。

不修改 `pending_memories` 表 schema——scope 通过 metadata JSON 传递即可，pending 表是短期队列。

## 修改清单

### 1. 材质化线程 — `src/superlocalmemory/server/unified_daemon.py`

**`_loop()` 函数**，在创建 `AtomicFact` 之前从 `md` 提取 scope：

```python
# 现有
md = _json.loads(md_str) if md_str else {}

# 新增
scope = md.pop("scope", "personal")
shared_with_raw = md.pop("shared_with", None)
shared_with = shared_with_raw if isinstance(shared_with_raw, list) else None
```

`AtomicFact` 构造加上 scope 和 shared_with：

```python
fact = AtomicFact(
    content=content,
    fact_type=FactType.EPISODIC,
    memory_id=mem_id,
    profile_id=engine._profile_id,
    scope=scope,              # 新增
    shared_with=shared_with,  # 新增
)
```

`memories` INSERT 加上 `scope` 和 `shared_with` 列：

```python
engine._db.execute(
    "INSERT OR IGNORE INTO memories "
    "(memory_id, profile_id, content, "
    "session_id, speaker, role, created_at, "
    "scope, shared_with, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
    (mem_id, engine._profile_id, content,
     "", "", "user",
     datetime.now(timezone.utc).isoformat(),
     scope,
     _json.dumps(shared_with) if shared_with else None,
     _json.dumps(md)),
)
```

**`RememberRequest` model** 加字段：

```python
class RememberRequest(BaseModel):
    content: str
    tags: str = ""
    scope: str = "personal"          # 新增
    shared_with: str = ""            # 新增
    metadata: dict | None = None

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in ("personal", "global", "shared"):
            raise ValueError(f"Invalid scope '{v}', must be personal/global/shared")
        return v
```

**`/remember` 端点**，两条分支均传递 scope：

```python
# 解析 shared_with（两端点共用）
parsed_shared = (
    [s.strip() for s in req.shared_with.split(",") if s.strip()]
    if req.shared_with else None
)

# Sync 分支（wait=True）
if wait:
    metadata = {"tags": req.tags} if req.tags else {}
    extra = getattr(req, "metadata", None)
    if isinstance(extra, dict):
        metadata.update(extra)
    fact_ids = engine.store(
        req.content,
        metadata=metadata,
        scope=req.scope,              # 新增
        shared_with=parsed_shared,    # 新增
    )
    return {"ok": True, "fact_ids": fact_ids, "count": len(fact_ids)}

# Async 分支（默认）
meta = {}
if req.tags:
    meta["tags"] = req.tags
meta["scope"] = req.scope
if parsed_shared:
    meta["shared_with"] = parsed_shared
extra = getattr(req, "metadata", None)
if isinstance(extra, dict):
    meta.update(extra)
pending_id = store_pending(req.content, tags=req.tags or "", metadata=meta)
```

### 2. CLI 参数 — `src/superlocalmemory/cli/main.py`

`remember` 子命令新增两个参数：

```python
remember_p.add_argument("--scope", default="personal",
    choices=["personal", "global", "shared"],
    help="Memory scope (default: personal)")
remember_p.add_argument("--shared-with", default="",
    help="Comma-separated agent IDs (only with scope=shared)")
```

### 3. CLI 命令逻辑 — `src/superlocalmemory/cli/commands.py`

`cmd_remember()` 三条路径均传递 scope：

**daemon 路径**：

```python
result = daemon_request("POST", "/remember", {
    "content": args.content,
    "tags": args.tags or "",
    "scope": args.scope,                            # 新增
    "shared_with": args.shared_with or "",           # 新增
})
```

**pending 路径**：

```python
metadata = {}
if args.tags:
    metadata["tags"] = args.tags
if args.scope and args.scope != "personal":
    metadata["scope"] = args.scope
if args.shared_with:
    metadata["shared_with"] = [s.strip() for s in args.shared_with.split(",") if s.strip()]

row_id = store_pending(
    content=args.content,
    tags=args.tags or "",
    metadata=metadata,
)
```

**sync 路径**：

```python
shared_with = [s.strip() for s in args.shared_with.split(",") if s.strip()] if args.shared_with else None
fact_ids = engine.store(
    args.content,
    metadata=metadata,
    scope=args.scope,              # 新增
    shared_with=shared_with,       # 新增
)
```

### 4. Dashboard 导入 API — `src/superlocalmemory/server/routes/data_io.py`

`/api/import` 端点从 JSON 读取 scope，传给 `engine.store()`：

```python
scope = memory.get("scope", "personal")
if scope not in ("personal", "global", "shared"):
    errors.append(f"Memory {idx}: invalid scope '{scope}'")
    continue
shared_with = memory.get("shared_with")

if engine:
    engine.store(
        content=memory_content,
        session_id=memory.get("session_id", ''),
        scope=scope,
        shared_with=shared_with,
        metadata={
            "project_name": memory.get("project_name"),
            "category": memory.get("category"),
            "tags": memory.get("tags", ''),
        },
    )
else:
    # Fallback: 直接 DB INSERT。
    # 注意：此路径缺少 fact_id/memory_id（预存 bug，非本次引入），
    # 实际运行时 engine 不可用时此分支大概率也会失败。
    # 此处保持与现有代码一致的最低限度处理。
    errors.append(f"Memory {idx}: engine unavailable, scope not applied")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO atomic_facts (content, profile_id, session_id, scope) "
        "VALUES (?, ?, ?, 'personal')",
        (memory_content, get_active_profile(), memory.get('session_id', '')),
    )
    conn.commit()
    conn.close()
```

## scope 值校验

所有入口均需校验 scope 合法值（`personal` / `global` / `shared`）：

| 入口 | 校验方式 |
| ------- | ---------- |
| CLI `--scope` | argparse `choices=["personal", "global", "shared"]` |
| Daemon `/remember` | Pydantic `@field_validator`，非法值返回 422 |
| Dashboard `/api/import` | 逐条检查，非法 scope 记入 errors 列表跳过 |
| 材质化线程 | 不校验（信任上游已校验），未知 scope 保留原值 |

## 不修改的部分

| 模块 | 原因 |
|------|------|
| `cli/pending_store.py` | scope 通过 metadata JSON 传递，无需改 API |
| `pending_memories` 表 schema | 短期队列，不加列 |
| `mcp/tools_core.py` | 已正确把 scope 写入 metadata |
| `core/engine.py` `store()` | 已正确处理 scope |
| `core/store_pipeline.py` `run_store()` | 已正确设置 `fact.scope` |

## 测试计划

| 测试 | 验证点 |
|------|--------|
| 材质化线程单元测试 | `store_pending` 带 scope metadata → 模拟材质化读取 → `AtomicFact.scope` 正确 |
| CLI 集成测试 | `slm remember --scope global "test"` → `slm recall "test" --json` → scope=global |
| CLI shared 测试 | `slm remember --scope shared --shared-with agent1 "test"` → shared_with 正确 |
| Dashboard 导入测试 | POST `/api/import` 带 scope → 验证 atomic_facts.scope 值 |
| 回归测试 | 不传 scope → 默认 personal，不破坏现有行为 |
| MCP 异步测试 | MCP remember 带 scope → pending → 材质化 → scope 正确写入 |

## 向后兼容

- 所有新参数都有默认值 `personal` / `""`
- 现有不传 scope 的调用不受影响
- `pending_memories` 表无 schema 变更，无需迁移
