# 三层作用域端到端传递 — 实施计划

> 2026-05-15 | SuperLocalMemory V3.4.45+  
> 对应 Spec：`docs/superpowers/specs/2026-05-15-scope-e2e-design.md`

---

## 目标

修复 scope 参数在异步路径（MCP → pending → 材质化线程、CLI、Dashboard）中的丢失问题，使 `personal` / `global` / `shared` 三层作用域在**所有入口**都能正确写入 `atomic_facts.scope`。

**不修改：** `pending_memories` 表 schema、`mcp/tools_core.py`、`core/engine.py` 的 `store()`、`core/store_pipeline.py` 的 `run_store()`。

---

## 任务总览

| 编号 | 任务 | 涉及文件 | 依赖 |
|:---:|:---|:---|:---:|
| T1 | 材质化线程 `_loop()` 提取 scope/shared_with + **单元测试** | `server/unified_daemon.py`, `tests/test_unified_daemon_materializer.py` | — |
| T2 | Daemon `/remember` 端点传递 scope + **daemon scope 测试** | `server/unified_daemon.py`, `tests/test_daemon_remember_scope.py` | T1 |
| T3 | CLI `main.py` 新增 `--scope`/`--shared-with` 参数 + `commands.py` 三条路径传递 scope + **CLI 集成测试** | `cli/main.py`, `cli/commands.py`, `tests/test_cli_scope.py` | — |
| T4 | Dashboard `/api/import` 读取 scope 并传递 + **Dashboard import 测试** | `server/routes/data_io.py`, `tests/test_dashboard_import_scope.py` | — |
| T5 | 回归测试 + MCP 全链路集成测试 | `tests/test_scope_regression.py` | T1, T2, T3, T4 |

---

## 任务详情

### T1：材质化线程 `_loop()` 提取 scope/shared_from + 单元测试

**目标：** 在材质化线程从 `pending_memories.metadata` JSON 中读取 `scope` 和 `shared_with`，写入 `AtomicFact` 和 `memories` 表。

**涉及文件：**
- `src/superlocalmemory/server/unified_daemon.py`（1 处修改）
- `tests/test_unified_daemon_materializer.py`（新建，T6 原测试文件）

**具体修改范围：**

1. 在 `_loop()` 中 `md = _json.loads(md_str)` 之后新增提取逻辑：

```python
scope = md.pop("scope", "personal")
shared_with_raw = md.pop("shared_with", None)
shared_with = shared_with_raw if isinstance(shared_with_raw, list) else None
```

2. `AtomicFact` 构造加上 `scope` 和 `shared_with`：

```python
fact = AtomicFact(
    content=content,
    fact_type=FactType.EPISODIC,
    memory_id=mem_id,
    profile_id=engine._profile_id,
    scope=scope,
    shared_with=shared_with,
)
```

3. `memories` INSERT 加上 `scope` 和 `shared_with` 列：

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

**验证方式（代码修改后立即执行）：**

> 测试文件：`tests/test_unified_daemon_materializer.py`（原 T6）

```python
def test_materializer_extracts_scope_from_metadata():
    """pending 记录带 scope=global metadata → 材质化后 AtomicFact.scope=global"""
    # mock get_pending 返回 {"metadata": '{"scope": "global"}'}
    # 调用 _loop() 一次迭代
    # 断言 engine.store_fact_direct 被调用时 fact.scope == "global"

def test_materializer_extracts_shared_with():
    """pending 记录带 shared_with metadata → 材质化后 fact.shared_with 正确"""

def test_materializer_default_scope_personal():
    """pending 记录无 scope → 默认 personal"""

def test_materializer_unknown_scope_preserved():
    """上游未校验的未知 scope → 保留原值（材质化线程不校验）"""
```

运行：`pytest tests/test_unified_daemon_materializer.py -v`

---

### T2：Daemon `/remember` 端点传递 scope + daemon scope 测试

**目标：** 在 `RememberRequest` 模型新增字段，并在 sync/async 两条分支中传递 scope。

**涉及文件：**
- `src/superlocalmemory/server/unified_daemon.py`（2 处修改）
- `tests/test_daemon_remember_scope.py`（新建，原 T7 中 daemon 部分）

**具体修改范围：**

1. `RememberRequest` 模型加字段和 validator：

```python
from pydantic import BaseModel, field_validator

class RememberRequest(BaseModel):
    content: str
    tags: str = ""
    scope: str = "personal"
    shared_with: str = ""
    metadata: dict | None = None

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in ("personal", "global", "shared"):
            raise ValueError(f"Invalid scope '{v}', must be personal/global/shared")
        return v
```

2. `/remember` 端点，sync 分支（`wait=True`）：

```python
parsed_shared = (
    [s.strip() for s in req.shared_with.split(",") if s.strip()]
    if req.shared_with else None
)
fact_ids = engine.store(
    req.content,
    metadata=metadata,
    scope=req.scope,
    shared_with=parsed_shared,
)
```

3. `/remember` 端点，async 分支（默认）：

```python
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

**验证方式（代码修改后立即执行）：**

> 测试文件：`tests/test_daemon_remember_scope.py`（原 T7 中 daemon 部分）

```python
def test_remember_post_scope_global():
    """POST /remember 带 scope=global → 返回 ok，pending metadata 含 scope"""

def test_remember_post_invalid_scope_422():
    """POST /remember 带非法 scope → HTTP 422"""

def test_remember_post_async_scope_preserved():
    """POST /remember async 路径 → pending metadata 含 scope，材质化后 atomic_facts.scope 正确"""
```

运行：`pytest tests/test_daemon_remember_scope.py -v`

---

### T3：CLI 参数 + 命令传递 scope + CLI 集成测试

**目标：** `slm remember` 新增 `--scope`/`--shared-with` 参数，三条路径均传递 scope。

**涉及文件：**
- `src/superlocalmemory/cli/main.py`（1 处修改，原 T3）
- `src/superlocalmemory/cli/commands.py`（1 处修改，原 T4）
- `tests/test_cli_scope.py`（新建，原 T7 中 CLI 部分）

**具体修改范围：**

1. `main.py` 在 `remember_p.add_argument("--tags", ...)` 之后新增：

```python
remember_p.add_argument(
    "--scope", default="personal",
    choices=["personal", "global", "shared"],
    help="Memory scope (default: personal)")
remember_p.add_argument(
    "--shared-with", default="",
    help="Comma-separated agent IDs (only with scope=shared)")
```

2. `commands.py` **daemon 路径**：

```python
result = daemon_request("POST", "/remember", {
    "content": args.content,
    "tags": args.tags or "",
    "scope": args.scope,
    "shared_with": args.shared_with or "",
})
```

3. `commands.py` **pending 路径**：

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

4. `commands.py` **sync 路径**：

```python
shared_with = [s.strip() for s in args.shared_with.split(",") if s.strip()] if args.shared_with else None
fact_ids = engine.store(
    args.content,
    metadata=metadata,
    scope=args.scope,
    shared_with=shared_with,
)
```

**验证方式（代码修改后立即执行）：**

> 测试文件：`tests/test_cli_scope.py`（原 T7 中 CLI 部分）

```python
def test_remember_cli_scope_global():
    """slm remember --scope global → recall 结果 scope=global"""

def test_remember_cli_scope_shared():
    """slm remember --scope shared --shared-with a1,a2 → shared_with 正确"""

def test_remember_cli_default_scope():
    """不传 --scope → 默认 personal"""

def test_remember_cli_invalid_scope():
    """slm remember --scope invalid → argparse 报错"""
```

运行：`pytest tests/test_cli_scope.py -v`

---

### T4：Dashboard `/api/import` 读取 scope + Dashboard import 测试

**目标：** 从导入 JSON 中读取 `scope` 和 `shared_with`，传给 `engine.store()`。

**涉及文件：**
- `src/superlocalmemory/server/routes/data_io.py`（1 处修改，原 T5）
- `tests/test_dashboard_import_scope.py`（新建，原 T7 中 Dashboard 部分）

**具体修改范围：**

在 `for idx, memory in enumerate(memories):` 循环内，在 `memory_content` 校验之后新增：

```python
scope = memory.get("scope", "personal")
if scope not in ("personal", "global", "shared"):
    errors.append(f"Memory {idx}: invalid scope '{scope}'")
    continue
shared_with = memory.get("shared_with")
```

然后修改 `engine.store()` 调用：

```python
engine.store(
    content=memory_content,
    session_id=memory.get('session_id', ''),
    scope=scope,
    shared_with=shared_with,
    metadata={
        "project_name": memory.get('project_name'),
        "category": memory.get('category'),
        "tags": memory.get('tags', ''),
    },
)
```

**验证方式（代码修改后立即执行）：**

> 测试文件：`tests/test_dashboard_import_scope.py`（原 T7 中 Dashboard 部分）

```python
def test_import_with_scope():
    """POST /api/import 含 scope → atomic_facts.scope 正确"""

def test_import_invalid_scope_skipped():
    """非法 scope → 记入 errors，不写入"""
```

运行：`pytest tests/test_dashboard_import_scope.py -v`

---

### T5：回归测试 + MCP 全链路集成测试

**目标：** 验证旧调用不破坏，以及 MCP → pending → 材质化完整链路。

**涉及文件：**
- `tests/test_scope_regression.py`（新建或追加到现有，原 T8）

**测试用例：**

```python
def test_remember_without_scope_defaults_personal():
    """旧调用（无 scope）→ 默认 personal，不破坏现有行为"""

def test_mcp_remember_async_scope_preserved():
    """MCP remember 带 scope → pending → 材质化 → scope 正确写入"""
    # mock mcp/tools_core.py 的 remember 调用（已写入 metadata）
    # 触发材质化
    # 断言 atomic_facts.scope 正确

def test_cross_module_scope_consistency():
    """CLI/Daemon/Dashboard 三条路径写入同 scope → 数据库结果一致"""
```

**验证方式：** `pytest tests/test_scope_regression.py -v`

---

## 依赖关系

```
T1 ──→ T2
 │
 └────→ T5（回归测试需要 T1+T2+T3+T4 全部完成）

T3 ──→ T5

T4 ──→ T5
```

- **T1 必须先于 T2**：材质化线程必须先能读取 scope，端点传递才有意义。
- **T5 依赖 T1、T2、T3、T4**：回归和 MCP 全链路测试需要所有代码模块完成。

**每个代码任务的测试不依赖其他任务**（T1 的测试只测材质化线程，T2 的测试 mock daemon，T3 的测试 mock CLI，T4 的测试 mock Dashboard），可独立运行。

**推荐执行顺序：** T1 → T2 → T3 → T4 → T5

---

## 测试文件说明

以下测试文件在计划中按任务分散执行，但物理上仍是独立文件：

| 文件 | 所属任务 | 说明 |
|:---|:---:|:---|
| `tests/test_unified_daemon_materializer.py` | T1 | 材质化线程单元测试 |
| `tests/test_daemon_remember_scope.py` | T2 | Daemon `/remember` scope 测试 |
| `tests/test_cli_scope.py` | T3 | CLI 集成测试 |
| `tests/test_dashboard_import_scope.py` | T4 | Dashboard import 测试 |
| `tests/test_scope_regression.py` | T5 | 回归 + MCP 全链路集成测试 |

---

## 回滚方案

1. **单文件回滚：** 每个任务仅修改 1-2 个文件，可单独 `git checkout <file>` 回滚。
2. **commit 粒度：** 每个任务独立 commit，回滚到任意任务点只需 `git revert <commit>`。
3. **紧急全回滚：**
   ```bash
   git log --oneline -10
   git revert HEAD~N..HEAD   # N = 已完成的任务数
   ```
4. **数据库层面：** 无 schema 变更，`pending_memories` 和 `atomic_facts` 表结构不变，回滚不涉及数据迁移。
5. **配置层面：** 新增参数均有默认值，回滚后旧调用不受影响。

---

## 风险点

| 风险 | 概率 | 影响 | 缓解措施 |
|:---|:---:|:---:|:---|
| `memories` 表缺少 `scope`/`shared_with` 列导致 INSERT 失败 | 中 | **高** | 实施前确认 schema 已有这两列（Phase 2 已添加）；如缺失需先补 schema |
| `AtomicFact` 的 `scope` 字段类型/默认值与 store_pipeline 不一致 | 低 | 中 | `AtomicFact` 已定义 `scope: str = "personal"`，与 spec 一致 |
| CLI `--shared-with` 与 `--scope` 组合使用时用户传错（如 scope=personal 却传 shared-with） | 中 | 低 | 不强制拦截（与现有行为一致），shared_with 仅在 scope=shared 时实际生效 |
| Dashboard `/api/import` fallback 路径（engine=None）仍不处理 scope | 低 | 低 | fallback 路径已标记为预存 bug，本次仅做最低限度处理（保持与现有代码一致） |
| 材质化线程 `_loop()` 异常导致 pending 堆积 | 低 | 高 | 现有 `try/except` 已捕获异常并 `mark_failed`，本次修改保持该模式 |
| Pydantic `@field_validator` 与 FastAPI 版本兼容 | 低 | 中 | 使用标准 Pydantic v2 API，仓库已依赖 pydantic >=2.0 |

---

## 校验矩阵

| 入口 | 校验方式 | 非法值行为 |
|:---|:---|:---|
| CLI `--scope` | `argparse choices=["personal", "global", "shared"]` | 报错并打印 help |
| Daemon `/remember` | Pydantic `@field_validator` | 返回 HTTP 422 |
| Dashboard `/api/import` | 逐条检查，非法 scope 记入 errors 列表 | 跳过该条，继续处理后续 |
| 材质化线程 | **不校验**（信任上游） | 未知 scope 保留原值 |

---

## 提交规范

```bash
# 每个任务完成后独立 commit（代码 + 对应测试一起提交）
git add <files>
git commit -m "plan: <任务编号> <简短描述>"

# 示例
git commit -m "plan: T1 materializer extracts scope/shared_with from metadata + unit tests"
git commit -m "plan: T2 daemon /remember endpoint passes scope in sync+async branches + daemon tests"
git commit -m "plan: T3 CLI adds --scope/--shared-with args, commands.py passes scope in all paths + CLI tests"
git commit -m "plan: T4 Dashboard /api/import reads scope from JSON + import tests"
git commit -m "plan: T5 regression tests + MCP async full-pipeline integration tests"
```

---

## 验收标准

- [ ] `slm remember --scope global "test"` → `atomic_facts.scope = "global"`
- [ ] `slm remember --scope shared --shared-with a1 "test"` → `shared_with = ["a1"]`
- [ ] MCP `remember` 带 scope → pending → 材质化 → scope 正确
- [ ] Dashboard `/api/import` 含 scope → scope 正确写入
- [ ] 不传 scope 的任何路径 → 默认 `personal`，不破坏现有行为
- [ ] 所有新增测试通过
- [ ] 现有回归测试通过（`pytest tests/ -q --tb=short -x`，排除已知失败的 migration_runner）
