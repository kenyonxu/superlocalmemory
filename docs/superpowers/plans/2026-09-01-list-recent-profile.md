# list_recent per-request profile 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `list_recent` 与 daemon `/list` 补可选 `profile_id` 穿透(与 PR #127 语义一致),结果完整(不截断+补 importance),顺带修复 `engine.list_facts` 缺失断点(daemon `/list` 当前生产 500)。

**Architecture:** 引擎补 `list_facts(limit, profile_id=None)` 入口(复用 `db.get_all_facts` 的参数化惯例 `pid = profile_id or self._profile_id`)→ daemon `/list` 加 `profile_id` 参数与 404 校验(同 Task 3 先例)→ MCP `list_recent` 加参数 + 结果完整度(去截断/补 importance)。纯追加,零行为变更于空参数路径。

**Tech Stack:** Python 3.13、FastAPI、pytest。所有 pytest 调用:`env -u ALL_PROXY -u all_proxy PYTHONPATH=<worktree>/src ~/miniconda3/bin/python -m pytest`(worktree 必须 PYTHONPATH 前置)。

**Spec:** `docs/superpowers/specs/2026-09-01-list-recent-profile-design.md`(决策:daemon 补齐 / 不截断 / 补 importance / 空命名空间走 success:true / allowlist 不变)

## Global Constraints

- **兼容锚点**:`profile_id` 空/None = 逐字节现状(活跃 profile),存量调用零改动。
- **纯路由**:非空时不读不改 `ProfileRuntime` 活跃指针与 `profile_generation`。
- **unknown_profile**:`success:false` + `error_code:"unknown_profile"` + HTTP 404,不隐式创建,不触引擎(与 Task 3 先例同构)。
- **结果完整**:`content` 不截断(移除 120/100 字符斩);`importance` 在场;`fact_type`/`created_at`/`session_id` 既有字段保留。
- **空命名空间**:`{success:true, results:[], count:0}`,无 abstain 文案。
- **allowlist 不变**:`SLM_MCP_TOOLS` 只管工具名,`list_recent` 已在域内;新参数无需变更。
- AGENTS.md 强制:提交前 gitnexus detect_changes(无 MCP 环境退化人工核对 diff 范围)。
- 全程在 worktree `/tmp/slm-lr`(分支 `feat/list-recent-profile`)进行,基于 main(已含 PR #127 的全部能力)。

---

### Task 0: 前置实证 + 基线

**Files:**
- 无改动;产出:断点实证记录 + 基线状态

**Interfaces:**
- Consumes: 无
- Produces: `/tmp/slm-lr-baseline.txt`(基线记录)

- [ ] **Step 1: 建 worktree**

```bash
cd $HOME/github/superlocalmemory
git status --porcelain   # 干净
git worktree add /tmp/slm-lr main -b feat/list-recent-profile
cd /tmp/slm-lr
```

- [ ] **Step 2: 实证 daemon /list 断点(30 秒)**

```bash
curl -s "http://127.0.0.1:8765/list?limit=1" | head -c 200
# 预期:500 或错误(engine.list_facts 在 4.1.x 不存在)
# 记录实际输出到 report(这坐实 spec 的"从坏到好"叙事)
```

- [ ] **Step 3: 基线测试**

```bash
cd /tmp/slm-lr && env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src \
  ~/miniconda3/bin/python -m pytest tests/ -q > /tmp/slm-lr-baseline.log 2>&1
# 后台+轮询;预期 0 failed(11095 基准 + 既有 sanctioned deselect 集合)
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src \
  ~/miniconda3/bin/python -m pytest src/superlocalmemory/integrations/hermes/tests -q
```

Expected: 全绿。若红先回报。

---

### Task 1: engine.list_facts 入口

**Files:**
- Modify: `src/superlocalmemory/core/engine.py`(加 `list_facts`,放 `recall` 附近)
- Test: `tests/test_core/test_engine_list_facts.py`(新建)

**Interfaces:**
- Consumes: `db.get_all_facts(pid, limit=...)`(既有,已参数化,LIMIT 下推、newest-first)
- Produces: `engine.list_facts(limit: int = CANONICAL_LIST_LIMIT, profile_id: str | None = None) -> list[AtomicFact]`。Task 2/3 消费。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_core/test_engine_list_facts.py
"""engine.list_facts: profile-threaded, newest-first, LIMIT push-down."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.core.engine import MemoryEngine


def _engine(tmp_path, profiles=("a", "b")) -> MemoryEngine:
    """按 test_core 现有引擎 fixture 惯例构造(真 DB,mock 重层)。"""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine_capabilities import Capabilities
    cfg = SLMConfig()
    cfg.base_dir = str(tmp_path)
    cfg.active_profile = "a"
    eng = MemoryEngine.__new__(MemoryEngine)
    # 复用现有 fixture 惯例初始化 DB 层(参考 test_engine_store_profile.py 的 engine_with_mock_deps)
    return eng


class TestListFacts:
    def test_explicit_profile_routes(self, tmp_path):
        eng = _engine(tmp_path)
        facts = eng.list_facts(limit=5, profile_id="b")
        # 断言:只返回 b 的 facts(写几条 a/b 后清点)

    def test_none_falls_back_to_active(self, tmp_path):
        eng = _engine(tmp_path)
        facts = eng.list_facts(limit=5)
        # 断言:落活跃 profile(a)

    def test_limit_pushed_down(self, tmp_path):
        eng = _engine(tmp_path)
        with patch.object(eng._db, "get_all_facts", wraps=eng._db.get_all_facts) as spy:
            eng.list_facts(limit=3)
        assert spy.call_args.kwargs.get("limit") == 3

    def test_active_pointer_untouched(self, tmp_path):
        eng = _engine(tmp_path)
        before = eng._profile_id
        eng.list_facts(limit=5, profile_id="b")
        assert eng._profile_id == before
```

(注:fixture 复用 `tests/test_core/` 现有惯例,实施者在 report 记录采用了哪个。断言意图不变:显式→目标、空→活跃、LIMIT 下推、指针不动。)

- [ ] **Step 2: 确认失败**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src ~/miniconda3/bin/python -m pytest tests/test_core/test_engine_list_facts.py -q`
Expected: FAIL(AttributeError: no list_facts)

- [ ] **Step 3: 实现**

在 `engine.py` 的 `recall` 附近加:

```python
    def list_facts(
        self, limit: int = CANONICAL_LIST_LIMIT, profile_id: str | None = None,
    ) -> list:
        """List facts newest-first, optionally routed to a specific profile."""
        pid = profile_id or self._profile_id
        return self._db.get_all_facts(pid, limit=limit)
```

- [ ] **Step 4: 跑测试 + test_core 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src ~/miniconda3/bin/python -m pytest tests/test_core/test_engine_list_facts.py tests/test_core/ -q`
Expected: 新 4 项 PASS + test_core 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/core/engine.py tests/test_core/test_engine_list_facts.py
git commit -m "feat(engine): list_facts entry with per-request profile threading"
```

---

### Task 2: daemon /list 穿透 + 修复 + 结果完整

**Files:**
- Modify: `src/superlocalmemory/server/unified_daemon.py`(/list ~5455)
- Test: `tests/test_server/test_list_recent_profile.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `engine.list_facts(limit, profile_id=None)`;Task 3 先例的 `_profile_exists` 校验辅助与 `unknown_profile` 错误体(若 Task 3 的辅助不存在,按同一形态加一个最小的 `_daemon_profile_exists` helper——与 /remember /recall 共用)
- Produces: `GET /list?profile_id=&limit=` → `{results:[{fact_id,content,fact_type,created_at,importance}],count}`;content 不截断;ghost → 404 + unknown_profile

- [ ] **Step 1: 写失败测试(沿用 tests/test_server 的 daemon TestClient fixture 惯例)**

```python
# tests/test_server/test_list_recent_profile.py
"""Per-request profile on /list + result completeness (spec §5)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def daemon(tmp_path):
    # 复用 test_per_request_profile.py 的 daemon fixture(真 TestClient + 双 profile)
    ...


class TestListRecentRouting:
    def test_routes_to_explicit_profile(self, daemon):
        client, _ = daemon
        # 写一条 >120 字符的到 doris
        client.post("/remember", json={"content": "x" * 200, "profile_id": "b"})
        r = client.get("/list", params={"profile_id": "b", "limit": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["results"]
        assert body["results"][0]["content"] == "x" * 200   # 不截断
        assert "importance" in body["results"][0]           # 在场

    def test_isolation(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "doris only", "profile_id": "b"})
        r = client.get("/list", params={"profile_id": "a"})
        assert all(f["content"] != "doris only" for f in r.json()["results"])

    def test_pointer_untouched(self, daemon):
        client, _ = daemon
        s0 = client.get("/status").json()
        client.get("/list", params={"profile_id": "b"})
        s1 = client.get("/status").json()
        assert s1["profile"] == s0["profile"]
        assert s1["profile_generation"] == s0["profile_generation"]

    def test_unknown_profile_404(self, daemon):
        client, _ = daemon
        r = client.get("/list", params={"profile_id": "ghost"})
        assert r.status_code == 404
        body = r.json()
        assert body["success"] is False
        assert body["error"]["code"] == "unknown_profile"

    def test_empty_profile_legacy(self, daemon):
        client, _ = daemon
        r = client.get("/list", params={"limit": 1})
        assert r.status_code == 200
        # 落活跃 profile(与改前一致)

    def test_empty_namespace_success(self, daemon):
        client, _ = daemon
        # 新建一个空 profile c
        r = client.get("/list", params={"profile_id": "c"})
        assert r.status_code == 200
        assert r.json() == {"success": True, "results": [], "count": 0}
```

- [ ] **Step 2: 确认失败**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src ~/miniconda3/bin/python -m pytest tests/test_server/test_list_recent_profile.py -q`
Expected: FAIL(引擎 list_facts 不存在 / 无 profile_id 参数 / 无 importance / 截断)

- [ ] **Step 3: 实现**

`/list` 改为:

```python
    @application.get("/list")
    async def list_facts(
        limit: int = CANONICAL_LIST_LIMIT, profile_id: str = "",
    ):
        _update_activity()
        engine = _get_engine_or_503()
        req_profile = (profile_id or "").strip()
        if req_profile:
            if not _daemon_profile_exists(engine, req_profile):   # 与 /remember /recall 同构
                return JSONResponse(
                    {"success": False, "error": {"code": "unknown_profile",
                                                 "profile_id": req_profile,
                                                 "message": f"profile '{req_profile}' does not exist"}},
                    status_code=404,
                )
            pid = req_profile
        else:
            pid = None
        facts = engine.list_facts(limit=limit, profile_id=pid)
        items = [
            {
                "fact_id": f.fact_id,
                "content": f.content,          # 不截断
                "fact_type": getattr(f.fact_type, "value", str(f.fact_type)),
                "created_at": f.created_at,
                "importance": getattr(f, "importance", None),
            }
            for f in facts
        ]
        return {"success": True, "results": items, "count": len(items)}
```

- [ ] **Step 4: 跑测试 + test_server 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src ~/miniconda3/bin/python -m pytest tests/test_server/ -q`
Expected: 新 6 项 PASS + 既有全绿(含 test_per_request_profile.py)。

- [ ] **Step 5: 实证修复(原断点)**

```bash
# 在隔离测试数据根起 daemon(或复用 Task 0 的实证 curl),确认 /list 不再 500
```

- [ ] **Step 6: 提交**

```bash
git add src/superlocalmemory/server/unified_daemon.py tests/test_server/test_list_recent_profile.py
git commit -m "fix(daemon): repair /list (engine.list_facts) + per-request profile routing + result completeness"
```

---

### Task 3: MCP list_recent 工具面

**Files:**
- Modify: `src/superlocalmemory/mcp/tools_core.py`(list_recent ~537)
- Test: 扩充 `tests/test_server/test_list_recent_profile.py` 或 tests/test_mcp 现有文件

**Interfaces:**
- Consumes: Task 1 的 `engine.list_facts`;Task 2 的 daemon `/list` 参数
- Produces: MCP 工具 `list_recent(limit=20, profile_id="")`;空 = 活跃

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_server/test_list_recent_profile.py
class TestMcpListRecent:
    def test_tool_accepts_profile_id(self, mcp_server):
        result = mcp_server.call_tool("list_recent", {"limit": 5, "profile_id": "b"})
        assert result["success"] is True
        assert result["results"]
        assert "importance" in result["results"][0]

    def test_schema_allows_optional_param(self, mcp_server):
        schema = mcp_server.get_tool_schema("list_recent")
        assert "profile_id" in schema["inputSchema"]["properties"]
        assert "profile_id" not in schema["inputSchema"].get("required", [])

    def test_no_profile_id_legacy(self, mcp_server):
        result = mcp_server.call_tool("list_recent", {"limit": 5})
        assert result["success"] is True
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**

`list_recent` 签名加 `profile_id: str = ""`;`_runtime_profile(get_engine, explicit=profile_id)` 直接生效(非空即跳过 daemon `/status` 往返);daemon 在跑时走 `GET /list?profile_id=`,离线回落 `engine.list_facts(profile_id=profile_id or None)`。结果 items 不再截断,补 `importance`。

- [ ] **Step 4: 跑测试 + MCP 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src ~/miniconda3/bin/python -m pytest tests/test_mcp/ tests/test_server/test_list_recent_profile.py -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/mcp/tools_core.py tests/test_server/test_list_recent_profile.py
git commit -m "feat(mcp): expose optional profile_id on list_recent + result completeness"
```

---

### Task 4: MCP stdio 通路 + 回归门 + 文档

**Files:**
- Test: `tests/test_integration/test_list_recent_stdio.py`(新建,仿 test_per_request_profile_e2e 的 MCP stdio 模式)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 0–3 全部
- Produces: 终态验收

- [ ] **Step 1: MCP stdio 集成测试**

```python
# tests/test_integration/test_list_recent_stdio.py
# 仿 test_per_request_profile_e2e.py 的 MCP 子进程模式:
# SLM_MCP_TOOLS=remember,recall,list_recent + 隔离 SLM_DATA_DIR + ephemeral 端口
# 1) 含 list_recent 时调用全通(带 profile_id)
# 2) 不含时工具不可见(tools/list 断言)
```

- [ ] **Step 2: 全量回归门**

```bash
cd /tmp/slm-lr && env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src \
  ~/miniconda3/bin/python -m pytest tests/ -q <既定 deselect 们> > /tmp/slm-lr-final-gate.log 2>&1
# 后台+轮询;预期 0 failed(11095 基准 + 本特性新增)
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lr/src \
  ~/miniconda3/bin/python -m pytest src/superlocalmemory/integrations/hermes/tests -q
```

- [ ] **Step 3: gitnexus 核查(无 MCP 退化:`git diff main..HEAD --stat` 应仅含 engine/unified_daemon/tools_core/测试/CHANGELOG)**

- [ ] **Step 4: 文档与提交**

```markdown
# CHANGELOG.md 顶部:
## mslm 4.2.0+ — list_recent per-request profile + /list repair (2026-09-01)
- list_recent and /list accept an optional profile_id (same semantics as remember/recall);
  content is no longer truncated upstream; importance is included; empty namespaces return
  {success:true, results:[], count:0} without abstain. Repairs the daemon /list endpoint
  whose engine.list_facts call was missing since the 4.1.x engine refactor.
```

```bash
git add -A && git commit -m "test+docs: list_recent per-request profile acceptance and changelog"
```

---

## Self-Review 记录

- **Spec 覆盖**:§2 决策表(5 条)→ Global Constraints;§3 数据流 → T2/T3;§4 引擎/DB 落点 → T1;§5 错误表 → T2 六个测试;§6 验收 1–7 → T2(1–7)+ T4(stdio);上游打包(§7)→ 文档在 T4 CHANGELOG 标注,PR 本体不在本计划(等 #127 落地)。
- **Placeholder 扫描**:无 TBD/TODO;T1/T2 的 fixture 点(“复用现有惯例,report 记录”)是有意的实现发现步骤,断言意图已绑定为不变式;T4 stdio 测试明确指向 e2e 的既有模式。
- **类型一致性**:`profile_id: str | None = None`(engine)/ `profile_id: str = ""`(daemon query、MCP 工具)两档与 PR #127 全链一致;`unknown_profile` 错误体形态与 Task 3 先例逐字一致;`CANONICAL_LIST_LIMIT` 复用既有常量。
- **风险**:T0 实证步骤 30 秒坐实断点叙事;T2 的 `_daemon_profile_exists` 辅助与 Task 3 已有先例共用(若不存在则按同形态新增,report 记录)。
