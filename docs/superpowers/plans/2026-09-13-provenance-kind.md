# provenance_kind 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `AtomicFact` 增 `provenance_kind` 受控标注(world/private/curated/legacy,缺省 null),写入(remember 可选参数)、修订(update_memory 原位修订 scope/标注/profile_id)、读面(四读工具回显 + 策展扫描过滤)全链路支持。

**Architecture:** 数据模型加列(M052 迁移)→ engine/daemon 写入与修订边界做词表校验(写入归 null,扫描 400)→ 读面四工具 additive 回显 → daemon `/list` 与 MCP `list_recent` 增 scope/provenance_kind 过滤。词表与校验逻辑单一权威源在 `storage/models.py`。

**Tech Stack:** Python 3.13、FastAPI、pytest。所有 pytest:`env -u ALL_PROXY -u all_proxy PYTHONPATH=<worktree>/src ~/miniconda3/bin/python -m pytest`(PYTHONPATH 前置)。

**Spec:** `docs/superpowers/specs/2026-09-13-provenance-kind-design.md`(决策:provenance_kind 命名 / 词表通用化 / 写入归 null·扫描 400 / 扩展 update_memory / R5 双层过滤 / scope 迁移借 M016)

## Global Constraints

- **词表权威源**:`storage/models.py` 的 `PROVENANCE_KINDS` + `validate_provenance_kind()`;词表外写入归 null,扫描 400。
- **兼容锚点**:不带新参数的 remember/update_memory/list_recent 调用行为字节不变。
- **选择性更新**:update_memory 只传提供的参数,空值不改;content 缺省时修订合法。
- **穿透锚点**:update_memory/delete_memory 的 `profile_id` 语义与 remember/recall/list_recent 完全一致(带则只作用该 profile、不读不改全局档)。
- **不加新工具**:allowlist 域不变(扩既有 update_memory/delete_memory 参数面)。
- **迁移**:M052 additive(IF NOT EXISTS),存量 NULL,无 backfill。
- **词表通用化**:不带 maid- 前缀;SLM 不背平台规则。
- AGENTS.md 强制:提交前 gitnexus detect_changes(无 MCP 环境退化人工核对 diff 范围)。
- 全程在 worktree `/tmp/slm-prov`(分支 `feat/provenance-kind`)进行,基于 main(已含 4.1.17 merge)。

---

### Task 1: 数据模型 + 迁移 + DB 层

**Files:**
- Modify: `src/superlocalmemory/storage/models.py`(AtomicFact 加字段 + PROVENANCE_KINDS + validate)
- Create: `src/superlocalmemory/storage/migrations/M052_provenance_kind_column.py`
- Modify: `src/superlocalmemory/storage/database.py`(_UPDATABLE_FACT_COLUMNS + get_all_facts 过滤参数)
- Test: `tests/test_storage/test_provenance_kind.py`(新建)

**Interfaces:**
- Consumes: 既有 `get_all_facts(profile_id, limit, *, include_global, include_shared)`
- Produces: `AtomicFact.provenance_kind: str | None`;`PROVENANCE_KINDS`/`validate_provenance_kind()`;M052 迁移;`db.get_all_facts(..., scope: str | None = None, provenance_kind: str | None = None, provenance_kind_null: bool = False)`;`_UPDATABLE_FACT_COLUMNS` 含 `provenance_kind`。Task 2/3/4 消费。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_storage/test_provenance_kind.py
"""provenance_kind: controlled vocabulary, storage column, DB filtering."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import (
    PROVENANCE_KINDS,
    AtomicFact,
    validate_provenance_kind,
)


class TestVocabulary:
    def test_four_values(self):
        assert PROVENANCE_KINDS == frozenset({"world", "private", "curated", "legacy"})

    def test_validates_in_vocabulary(self):
        for v in ("world", "private", "curated", "legacy"):
            assert validate_provenance_kind(v) == v

    def test_out_of_vocabulary_returns_none(self):
        assert validate_provenance_kind("garbage") is None
        assert validate_provenance_kind("maid-private") is None

    def test_none_and_empty_return_none(self):
        assert validate_provenance_kind(None) is None
        assert validate_provenance_kind("") is None
        assert validate_provenance_kind("  ") is None

    def test_normalizes_case_and_whitespace(self):
        assert validate_provenance_kind(" World ") == "world"


class TestStorageColumn:
    def test_atomic_fact_field_defaults_none(self):
        f = AtomicFact()
        assert f.provenance_kind is None

    def test_migration_additive_and_idempotent(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "t.db"))
        db.initialize()
        # M052 已跑过;再跑不炸(additive IF NOT EXISTS)
        from superlocalmemory.storage.migrations.M052_provenance_kind_column import run as run_m052
        run_m052(db)
        cols = [r[1] for r in db.execute("PRAGMA table_info(atomic_facts)").fetchall()]
        assert "provenance_kind" in cols
        # 存量(如有)为 NULL
        db.close()


class TestDBFiltering:
    def test_get_all_facts_scope_filter(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "t.db"))
        db.initialize()
        # 写 a:world/global, b:curated/global, c:None/global, d:world/personal
        facts = db.get_all_facts("a", scope="global")
        assert {f.content for f in facts} == {"a", "b", "c"}

    def test_get_all_facts_provenance_kind_filter(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "t.db"))
        db.initialize()
        facts = db.get_all_facts("a", scope="global", provenance_kind="world")
        assert {f.content for f in facts} == {"a"}

    def test_get_all_facts_provenance_null_filter(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "t.db"))
        db.initialize()
        facts = db.get_all_facts("a", scope="global", provenance_kind_null=True)
        assert {f.content for f in facts} == {"c"}

    def test_update_fact_provenance_kind_in_updatable_columns(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "t.db"))
        db.initialize()
        db.update_fact(fid, {"provenance_kind": "curated"}, profile_id="a")
        assert db.get_fact(fid, "a").provenance_kind == "curated"
        # 清标注
        db.update_fact(fid, {"provenance_kind": None}, profile_id="a")
        assert db.get_fact(fid, "a").provenance_kind is None
```

(注:`fid` 播种按 tests/test_storage 现有惯例;report 记录采用的播种方式。)

- [ ] **Step 2: 确认失败**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prov/src ~/miniconda3/bin/python -m pytest tests/test_storage/test_provenance_kind.py -q`
Expected: FAIL(字段/常量/迁移/过滤不存在)

- [ ] **Step 3: 实现**

models.py:
```python
PROVENANCE_KINDS: Final[frozenset[str]] = frozenset({
    "world", "private", "curated", "legacy",
})

def validate_provenance_kind(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    return v if v in PROVENANCE_KINDS else None

# AtomicFact 加:
provenance_kind: str | None = None   # 治理门禁标注,受控词表;None=未标注
```

M052(沿 M050/M051 惯例,additive):
```python
"""provenance_kind: controlled governance tag on the retrieval unit."""
def run(db) -> None:
    db.execute(
        "ALTER TABLE atomic_facts ADD COLUMN provenance_kind TEXT"
    ) if "provenance_kind" not in [r[1] for r in db.execute("PRAGMA table_info(atomic_facts)").fetchall()] else None
```

database.py:
- `_UPDATABLE_FACT_COLUMNS` 加 `"provenance_kind"`
- `get_all_facts` 加 `scope: str | None = None, provenance_kind: str | None = None, provenance_kind_null: bool = False`;where 拼接加 `scope = ?` / `provenance_kind = ?` / `provenance_kind IS NULL` 三个可选条件

- [ ] **Step 4: 跑测试 + test_storage 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prov/src ~/miniconda3/bin/python -m pytest tests/test_storage/ -q`
Expected: 新测试全过 + test_storage 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/storage/models.py src/superlocalmemory/storage/migrations/M052_provenance_kind_column.py src/superlocalmemory/storage/database.py tests/test_storage/test_provenance_kind.py
git commit -m "feat(storage): provenance_kind controlled tag on AtomicFact + M052 column + DB filtering"
```

---

### Task 2: 写入面(remember provenance_kind)

**Files:**
- Modify: `src/superlocalmemory/core/engine.py`(store/store_fast 加参数)
- Modify: `src/superlocalmemory/server/unified_daemon.py`(/remember body + 词表校验)
- Modify: `src/superlocalmemory/mcp/tools_core.py`(remember 签名)
- Test: `tests/test_server/test_provenance_surface.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `validate_provenance_kind`;既有 remember 链路的 profile_id 穿透惯例
- Produces: `remember(..., provenance_kind: str = "")`(MCP 工具)/ `/remember` body `provenance_kind`(daemon)/ `engine.store(..., provenance_kind=None)`(引擎)。Task 3/4 同惯例复用。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_server/test_provenance_surface.py
"""Write/read surface for provenance_kind (spec §4/§5)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def daemon(tmp_path):
    # 复用 test_per_request_profile.py 的 daemon fixture
    ...


class TestRememberProvenance:
    def test_writes_with_provenance_kind(self, daemon):
        client, _ = daemon
        r = client.post("/remember", json={
            "content": "world fact", "provenance_kind": "world",
        })
        assert r.status_code == 200
        fid = r.json()["fact_ids"][0]
        # 读回验证标注在场(用 fetch 或 /list)
        f = client.get("/list", params={"limit": 1}).json()["results"][0]
        assert f["provenance_kind"] == "world"

    def test_out_of_vocabulary_becomes_null(self, daemon):
        client, _ = daemon
        client.post("/remember", json={
            "content": "garbage tag", "provenance_kind": "garbage",
        })
        f = client.get("/list", params={"limit": 1}).json()["results"][0]
        assert f["provenance_kind"] is None

    def test_no_param_defaults_null(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "plain fact"})
        f = client.get("/list", params={"limit": 1}).json()["results"][0]
        assert f["provenance_kind"] is None

    def test_profile_routing_still_works(self, daemon):
        client, _ = daemon
        client.post("/remember", json={
            "content": "doris world", "profile_id": "b", "provenance_kind": "world",
        })
        f = client.get("/list", params={"profile_id": "b", "limit": 1}).json()["results"][0]
        assert f["provenance_kind"] == "world"
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**

- daemon `/remember` body 加 `provenance_kind: str = ""`;`validate_provenance_kind` 校验后传给 engine.store
- engine.store/store_fast 加 `provenance_kind: str | None = None` keyword-only(同 profile_id 惯例)
- MCP remember 签名加 `provenance_kind: str = ""`;透传 daemon body / 离线回落

- [ ] **Step 4: 跑测试 + test_server 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prov/src ~/miniconda3/bin/python -m pytest tests/test_server/ -q`
Expected: 新 4 项 PASS + test_server 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/core/engine.py src/superlocalmemory/server/unified_daemon.py src/superlocalmemory/mcp/tools_core.py tests/test_server/test_provenance_surface.py
git commit -m "feat(remember): optional provenance_kind on write path (daemon/engine/MCP)"
```

---

### Task 3: 修订面(update_memory/delete_memory 扩展)

**Files:**
- Modify: `src/superlocalmemory/mcp/tools_core.py`(update_memory/delete_memory 签名)
- Modify: `src/superlocalmemory/server/unified_daemon.py`(PATCH /api/memories/{id} 加参数)
- Modify: `src/superlocalmemory/core/engine.py`(update_fact 的校验辅助,如需)
- Test: `tests/test_server/test_provenance_surface.py`(扩充)

**Interfaces:**
- Consumes: Task 1 的 `_UPDATABLE_FACT_COLUMNS`(含 provenance_kind/scope/shared_with);Task 2 的 daemon 校验惯例;既有 update_memory 的 PATCH 通道(daemon)与 WorkerPool(离线)
- Produces: `update_memory(fact_id, content?, provenance_kind?, scope?, profile_id="")`;`delete_memory(fact_id, profile_id="")`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_server/test_provenance_surface.py
class TestUpdateMemoryProvenance:
    def test_migrate_scope_and_tag_in_place(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "to curate", "scope": "personal"})
        fid = client.get("/list", params={"limit": 1}).json()["results"][0]["fact_id"]
        r = client.patch(f"/api/memories/{fid}", json={
            "scope": "global", "provenance_kind": "curated",
        })
        assert r.status_code == 200
        f = client.get("/list", params={"limit": 1}).json()["results"][0]
        assert f["scope"] == "global"
        assert f["provenance_kind"] == "curated"
        assert f["content"] == "to curate"   # 原文不变

    def test_content_optional(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "original"})
        fid = client.get("/list", params={"limit": 1}).json()["results"][0]["fact_id"]
        r = client.patch(f"/api/memories/{fid}", json={"provenance_kind": "world"})
        assert r.status_code == 200
        assert client.get("/list", params={"limit": 1}).json()["results"][0]["content"] == "original"

    def test_clear_tag_with_null(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "tagged", "provenance_kind": "world"})
        fid = client.get("/list", params={"limit": 1}).json()["results"][0]["fact_id"]
        client.patch(f"/api/memories/{fid}", json={"provenance_kind": ""})  # 空串清标注
        assert client.get("/list", params={"limit": 1}).json()["results"][0]["provenance_kind"] is None

    def test_profile_routing(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "doris x", "profile_id": "b"})
        fid = client.get("/list", params={"profile_id": "b", "limit": 1}).json()["results"][0]["fact_id"]
        r = client.patch(f"/api/memories/{fid}?profile_id=b", json={"provenance_kind": "curated"})
        assert r.status_code == 200
        s0 = client.get("/status").json()
        f = client.get("/list", params={"profile_id": "b", "limit": 1}).json()["results"][0]
        assert f["provenance_kind"] == "curated"
        s1 = client.get("/status").json()
        assert s1["profile"] == s0["profile"] and s1["profile_generation"] == s0["profile_generation"]

    def test_legacy_active_profile_constraint_without_param(self, daemon):
        client, _ = daemon
        # 不带 profile_id:fact_id 属于别的 profile 时维持现状的拒绝语义(或按既有行为)
        ...
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**

daemon PATCH `/api/memories/{fact_id}` body 加可选 `provenance_kind`/`scope`/`profile_id`(query 参数,同 recall 惯例);校验(词表、scope 值、选择性更新)后调 engine.update_fact;content 缺省时不传 content 键。MCP update_memory/delete_memory 签名加 `profile_id: str = ""`,daemon 在跑走 PATCH,离线走 WorkerPool(同 update_memory 既有形状)。

- [ ] **Step 4: 跑测试 + test_server 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prov/src ~/miniconda3/bin/python -m pytest tests/test_server/ -q`
Expected: 新 5 项 PASS + test_server 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/mcp/tools_core.py src/superlocalmemory/server/unified_daemon.py src/superlocalmemory/core/engine.py tests/test_server/test_provenance_surface.py
git commit -m "feat(update_memory): in-place scope migration + provenance_kind + profile_id threading"
```

---

### Task 4: 读面回显 + 策展扫描过滤

**Files:**
- Modify: `src/superlocalmemory/mcp/tools_core.py`(recall/search/fetch/list_recent 结果项)
- Modify: `src/superlocalmemory/server/unified_daemon.py`(/list + /recall 结果项 + 过滤参数)
- Modify: `src/superlocalmemory/core/engine.py`(list_facts 透传过滤)
- Test: `tests/test_server/test_provenance_surface.py`(扩充)

**Interfaces:**
- Consumes: Task 1 的 `get_all_facts` 过滤参数;Task 2/3 的结果项形状
- Produces: 四读工具结果项统一 +scope +provenance_kind;`/list` 与 `list_recent` 增 `scope`/`provenance_kind`/`provenance_kind_null` 过滤参数

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_server/test_provenance_surface.py
class TestReadEcho:
    def test_recall_results_carry_scope_and_provenance_kind(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "echo me", "scope": "global", "provenance_kind": "world"})
        r = client.get("/recall", params={"q": "echo me"})
        assert r.json()["results"][0]["scope"] == "global"
        assert r.json()["results"][0]["provenance_kind"] == "world"

    def test_fetch_carries_both(self, daemon): ...
    def test_list_recent_carries_both(self, daemon): ...


class TestCurationScan:
    def test_filter_scope_global(self, daemon):
        client, _ = daemon
        # 播种:2 global(world+curated) + 2 global(None) + 1 personal(world)
        r = client.get("/list", params={"scope": "global"})
        assert all(f["scope"] == "global" for f in r.json()["results"])
        assert len(r.json()["results"]) == 4

    def test_filter_provenance_null(self, daemon):
        client, _ = daemon
        r = client.get("/list", params={"scope": "global", "provenance_kind": "null"})
        assert len(r.json()["results"]) == 2
        assert all(f["provenance_kind"] is None for f in r.json()["results"])

    def test_filter_provenance_world(self, daemon):
        client, _ = daemon
        r = client.get("/list", params={"scope": "global", "provenance_kind": "world"})
        assert len(r.json()["results"]) == 1
        assert r.json()["results"][0]["provenance_kind"] == "world"

    def test_out_of_vocabulary_scan_400(self, daemon):
        client, _ = daemon
        r = client.get("/list", params={"provenance_kind": "garbage"})
        assert r.status_code == 400
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**

四读工具结果项 additive 加 `scope`/`provenance_kind`(从 AtomicFact 直接取,注意 recall 的 RetrievalResult 包装层序列化);`/list` 与 `list_recent` 加 `scope`/`provenance_kind` query 参数(`provenance_kind=null` 字面量 → `provenance_kind_null=True`);`engine.list_facts` 透传过滤参数;scope 值受控校验(personal/shared/global,词表外 400)。

- [ ] **Step 4: 跑测试 + test_server + test_mcp 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prov/src ~/miniconda3/bin/python -m pytest tests/test_server/ tests/test_mcp/ -q`
Expected: 新测试全过 + 两目录全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/mcp/tools_core.py src/superlocalmemory/server/unified_daemon.py src/superlocalmemory/core/engine.py tests/test_server/test_provenance_surface.py
git commit -m "feat(read): echo scope+provenance_kind on all read surfaces + curation-scan filtering"
```

---

### Task 5: scope 迁移缓存审计 + 全量回归门 + 文档

**Files:**
- Test: `tests/test_integration/test_provenance_e2e.py`(新建,仿 test_per_request_profile_e2e 模式)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 1–4 全部
- Produces: 终态验收

- [ ] **Step 1: scope 迁移缓存审计(写入 report,必要时修复)**

```bash
# 审计 scope 迁移对图缓存/邻接缓存/向量的影响:
# 1) 一条 personal 事实迁 global 后,邻接缓存的 scope_key 含 include_global/include_shared——
#    邻接缓存的 staleness 检查(边数/TTL)会重载,但要验证 scope 迁移后实体图里这条 fact
#    的边在 include_global=true 的槽里是否可见(scope 变了但边数据没重建?)
# 2) 向量索引(sqlite-vec)按 profile 分,scope 迁移是否影响 vec0 的 scope 键
# 3) BM25 索引是否按 scope 分片
# 产出:迁移后 recall 能否在 include_global=true 时命中(端到端断言),或发现失效点并修复
```

- [ ] **Step 2: 端到端集成测试**

```python
# tests/test_integration/test_provenance_e2e.py
# 真 daemon 子进程 + 隔离数据根:
# 1) remember 带 world → 三读工具回显
# 2) personal 迁 global 标 curated → 读回显 + include_global 召回命中(Step 1 审计的端到端断言)
# 3) 双 profile 带 profile_id 修订只作用该 profile,指针不动
# 4) 策展扫描 global+provenance_kind=null 恰返未标注
```

- [ ] **Step 3: 全量回归门**

```bash
cd /tmp/slm-prov && env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prov/src \
  ~/miniconda3/bin/python -m pytest tests/ -q -n 8 <既定 deselect 们> > /tmp/slm-prov-final-gate.log 2>&1
# 后台+轮询;预期 0 failed(4.1.17 基线 + 本特性新增)
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prov/src \
  ~/miniconda3/bin/python -m pytest src/superlocalmemory/integrations/hermes/tests -q
```

- [ ] **Step 4: gitnexus 核查(无 MCP 退化:`git diff main..HEAD --stat`)**

- [ ] **Step 5: 文档与提交**

```markdown
# CHANGELOG.md 顶部:
## mslm 4.2.0+ — provenance_kind controlled tagging (2026-09-13)
- AtomicFact gains a controlled `provenance_kind` tag (world/private/curated/legacy,
  default null): remember accepts it, update_memory migrates scope and annotates in
  place with profile_id threading, all read surfaces echo scope+provenance_kind, and
  daemon /list + MCP list_recent support curation-scan filtering (scope, provenance_kind,
  provenance_kind=null). Vocabulary is generic; SLM carries no platform rules.
```

```bash
git add -A && git commit -m "test+docs: provenance_kind acceptance and changelog"
```

---

## Self-Review 记录

- **Spec 覆盖**:§2 决策表(6 条)→ Global Constraints;§3 数据模型/迁移 → T1;§4 写入/修订面 → T2/T3;§5 读面/策展扫描 → T4;§6 验收 1–7 → T2(1,7)/T3(2,3,4)/T4(4,5,6)/T5(e2e);§7 上游打包(README/日期炸弹/bug 报告)→ 不在本计划(独立跟进)。
- **Placeholder 扫描**:无 TBD/TODO;T3 的 legacy 约束测试与 T1 的 fixture/播种方式标注为"按现有惯例,report 记录"(断言意图不变);T5 Step 1 的审计产出写入 report 是必须的(它是 spec §7 的 review 地雷,不能跳过)。
- **类型一致性**:`provenance_kind: str | None = None`(AtomicFact/DB)/ `provenance_kind: str = ""`(daemon body、MCP 工具)与 profile_id 的 None/空串两档先例一致;`provenance_kind_null: bool` 显式区分"不过滤"与"找 null";scope 值受控与 provenance_kind 词表校验在写入归 null、扫描 400 的分场景语义全文一致。
- **风险前移**:T5 Step 1 的 scope 迁移缓存审计是 spec 点名的 review 地雷,作为独立验收步骤,产出物强制进 report。
