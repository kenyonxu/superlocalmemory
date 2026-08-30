# per-request profile 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `remember`/`recall` 接受可选 `profile_id`,携带时纯路由到该 profile(不读不改全局活跃指针),不携带时逐字节现状;含 hermes provider pin 适配与邻接缓存 LRU。

**Architecture:** 请求级穿透(单引擎):engine.store 补 profile_id 参数(与 recall 的 `pid = profile_id or self._profile_id` 惯例一致)→ daemon `/remember` `/recall` 校验+路由(取代 4.1.x 的 409 守卫)→ MCP 工具面暴露 → hermes provider 默认 pin。邻接缓存单槽改 profile 键控 LRU 消交错抖动。

**Tech Stack:** Python 3.13、FastAPI、pytest。所有 pytest 调用:`env -u ALL_PROXY -u all_proxy PYTHONPATH=<worktree>/src ~/miniconda3/bin/python -m pytest`(conda editable 指向主仓,worktree 必须 PYTHONPATH 前置;长套件 run_in_background + tail 轮询,严禁单阻塞调用)。

**Spec:** `docs/superpowers/specs/2026-08-30-per-request-profile-design.md`(决策:A 穿透 / 路由取代守卫 / R4 不做 / pin 默认开;需求书 `docs/deepmaid-per-request-profile-需求书-2026-08-30.md` R1–R6)

## Global Constraints

- **兼容锚点**:`profile_id` 为空/None = 逐字节现状路径,存量客户端零改动零感知(验收 4)。
- **纯路由**:非空时不读不改 `ProfileRuntime` 的活跃指针与 `profile_generation`(验收 2 单独成测)。
- **unknown_profile**:`success:false` + `error_code:"unknown_profile"` + HTTP 404,不隐式创建,不触引擎(验收 5)。
- **R6**:`switch_profile`/`ProfileRuntime` 语义不变,不做 per-profile 引擎。
- **allowlist 不变**:新参数不进 `SLM_MCP_TOOLS` 工具集;`remember,recall` 最小集天然支持。
- **scope 正交**:`scope`/`shared_with`/`include_global`/`include_shared` 语义与透传零改动。
- **邻接 LRU**:`SLM_ADJ_CACHE_PROFILES` 默认 3;缓存键 `(profile_id, include_global, include_shared)`;staleness 逐槽。
- **pin**:`pin_profile` 默认 true;关闭时省略参数(跟随活跃指针);离线回落本进程引擎同样传参。
- AGENTS.md 强制:提交前 gitnexus detect_changes(无 MCP 环境退化人工核对 diff 范围)。
- 全程在 worktree `/tmp/slm-prp`(分支 `feat/per-request-profile`)进行;Task 0 的 merge 在同 worktree 内做。

---

### Task 0: merge upstream 4.1.11

**Files:**
- Modify: 全库(merge);策略处置见步骤
- 产出:merge commit 于 feat/per-request-profile 分支

**Interfaces:**
- Consumes: upstream/main @ 85483816(4.1.11);fork main @ 48d12269
- Produces: 已含 409 守卫、`RememberRequest.profile_id`、`_runtime_profile`、`engine.recall(profile_id)` 的基线,供 Task 1–5 消费

- [ ] **Step 1: 建 worktree 与分支**

```bash
cd $HOME/github/superlocalmemory
git status --porcelain   # 必须干净
git worktree add /tmp/slm-prp main -b feat/per-request-profile
cd /tmp/slm-prp && git fetch upstream --tags
```

- [ ] **Step 2: 基线指纹(overlap 预判)**

```bash
MB=$(git merge-base HEAD upstream/main)   # 期望 f478873c
git diff --name-only $MB..HEAD | sort > /tmp/prp-fork-files.txt
git diff --name-only $MB..upstream/main | sort > /tmp/prp-upstream-files.txt
comm -12 /tmp/prp-fork-files.txt /tmp/prp-upstream-files.txt > /tmp/prp-overlap.txt
wc -l /tmp/prp-overlap.txt && cat /tmp/prp-overlap.txt
```

- [ ] **Step 3: 基线测试(合并前必须全绿;长套件后台+轮询)**

```bash
cd /tmp/slm-prp && env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src \
  ~/miniconda3/bin/python -m pytest tests/ -q > /tmp/prp-baseline.log 2>&1
# 轮询 tail /tmp/prp-baseline.log;预期 0 failed(既有 2 个 sanctioned deselect 若仍红,沿用其 node id)
```

- [ ] **Step 4: 执行 merge 与冲突处置(策略沿用 `docs/superpowers/plans/2026-08-21-merge-upstream-4.0.9.md` Task 2 的策略表)**

```bash
git merge --no-commit --no-ff upstream/main
```

处置规则(逐冲突文件):
- `src/superlocalmemory/core/embeddings.py` / `engine_wiring.py` / `config.py`:**手工合并**——fork 侧 proxy/fallback 补丁与上游 4.1.x 改动并存(方案 B 全部提交必须存活;以 `git show 48d12269:src/superlocalmemory/core/embeddings.py` 对照逐 hunk 验证)
- `src/superlocalmemory/integrations/hermes/`:上游无此目录,出现冲突即 `git merge --abort` 并 BLOCKED 上报
- 品牌层(pyproject/`__init__.py`/README/package.json/plugin*):**取 ours**(fork 品牌 + torch>=2.11.0;上游 pyproject 若有新增依赖按 8-21 计划的 jsonschema 先例评估:incoming 代码 import 它才加)
- `uv.lock`:删除后 `uv lock` 重生成
- 上游若再带 `dist_*/` 类构建产物:`git rm -rf` 剔除(`dist_*/` 已在 .gitignore)
- 零冲突标记残留后**不提交**,先跑 Step 5

- [ ] **Step 5: 存活核查(方案 B + 方案 A 七调用点)**

```bash
cd /tmp/slm-prp
grep -n "_try_attach_daemon_fallback\|_embed_via_daemon\|embedder_mode" src/superlocalmemory/core/embeddings.py | head -5
grep -c "_daemon_api" src/superlocalmemory/integrations/hermes/__init__.py   # ≥7
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest \
  tests/test_core/test_embedding_daemon_fallback.py tests/test_core/test_mcp_embedder_proxy.py -q
src/superlocalmemory/integrations/hermes/tests 2>/dev/null || true
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest \
  src/superlocalmemory/integrations/hermes/tests -q
```
Expected: fallback/proxy 测试全绿;hermes 92/92。任何缺失 → 恢复对应 fork hunk。

- [ ] **Step 6: 全量测试门 + 提交**

```bash
# 全量(后台+轮询,~50min);新红按 8-21 计划 Task 5 的分类法处置:
# 环境类→env 规避;上游新测试环境缺陷→deselect+记录;正当 fork 适配→修复
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/ -q \
  <既定 deselect 们> > /tmp/prp-merge-gate.log 2>&1
# 0 failed 后:
git add -A && git commit -m "merge: upstream SuperLocalMemory 4.1.10-4.1.11 into mslm fork (per-request profile baseline)"
```

---

### Task 1: engine.store profile_id 穿透

**Files:**
- Modify: `src/superlocalmemory/core/engine.py`(store/store_fact_direct/store_fast 三写路径)
- Test: `tests/test_core/test_engine_store_profile.py`(新建)

**Interfaces:**
- Consumes: `engine.recall` 的既有惯例 `pid = profile_id or self._profile_id`(engine.py ~1057)
- Produces: `engine.store(content, ..., profile_id: str | None = None)`(keyword-only,追加在签名尾部);`store_fact_direct(fact, profile_id=None)`;`store_fast(..., profile_id=None)`。Task 3/5 消费。

- [ ] **Step 1: 审计(写 failing test 前先产出穿透点清单,附进 report)**

```bash
cd /tmp/slm-prp
# 列出三写路径调用树中 self._profile_id 的全部使用点:
grep -n "self\._profile_id" src/superlocalmemory/core/engine.py | sed -n '1,60p'
# 逐点按 spec §4 判定:profile 作用域数据→换参;共享资源(embedder/LLM/reranker)→不动
# 产出表格(行号/用途/判定)写入 task-1-report.md
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_core/test_engine_store_profile.py
"""engine.store per-request profile threading (spec §4)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.core.engine import MemoryEngine


def _engine(tmp_path, profiles=("a", "b")) -> MemoryEngine:
    """Real DB layer, mocked heavy layer (no models in tests)."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine_capabilities import Capabilities
    cfg = SLMConfig()
    cfg.base_dir = str(tmp_path)
    cfg.active_profile = "a"
    eng = MemoryEngine.__new__(MemoryEngine)   # 按现有测试惯例构造轻量引擎
    # (按 test_core 现有引擎 fixture 惯例补齐 DB 层初始化;若已有可复用 fixture 则直接用)
    return eng


class TestStoreProfileThreading:
    def test_store_explicit_profile_lands_in_target(self, tmp_path):
        eng = _engine(tmp_path)
        with patch.object(eng, "_store_pipeline_run", return_value="fact-1") as run:
            eng.store("hello", profile_id="b")
        assert run.call_args.kwargs.get("profile_id") == "b" \
            or run.call_args.args.count("b") > 0   # 以审计后的实际签名断言

    def test_store_none_falls_back_to_active(self, tmp_path):
        eng = _engine(tmp_path)
        with patch.object(eng, "_store_pipeline_run", return_value="fact-1") as run:
            eng.store("hello", profile_id=None)
        # 断言收到的 profile_id == eng._profile_id

    def test_store_does_not_mutate_active_profile(self, tmp_path):
        eng = _engine(tmp_path)
        before = eng._profile_id
        eng.store("hello", profile_id="b")
        assert eng._profile_id == before
```

(注:mock 点 `_store_pipeline_run` 为示意——以 Step 1 审计出的真实下游入口为 mock/断言点;断言意图不变:显式→目标 profile,空→活跃 profile,活跃指针不变。fixture 复用 `tests/test_core/` 现有引擎构造惯例,实施者在 report 记录采用了哪个。)

- [ ] **Step 3: 跑测试确认失败**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/test_core/test_engine_store_profile.py -q`
Expected: FAIL(TypeError: unexpected keyword argument 'profile_id')

- [ ] **Step 4: 实现——三条写路径加 keyword-only `profile_id: str | None = None`,内部 `pid = profile_id or self._profile_id` 后,把 Step 1 清单中判定为"换参"的使用点全部改用 pid**

签名(三处一致,追加尾部):

```python
    def store(
        self,
        content: str,
        # ...既有参数不动...
        *,
        scope: str = "personal",
        shared_with: list[str] | None = None,
        profile_id: str | None = None,      # 新增,keyword-only
    ) -> str:
```

- [ ] **Step 5: 跑测试 + 既有写路径回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/test_core/test_engine_store_profile.py tests/test_core/ -q`
Expected: 新测试 PASS;test_core 全绿(空参数路径零回归)。

- [ ] **Step 6: materializer 归属验证(spec §4 第 3 点)**

```bash
grep -rn "profile_id" src/superlocalmemory/core/queue_consumer.py src/superlocalmemory/core/remember_runtime.py 2>/dev/null | head -8
# 确认 materializer 从 journal 行取 profile_id,不从 engine._profile_id 反推;
# 若发现反推点,一并换参并在 report 记录。
```

- [ ] **Step 7: 提交**

```bash
git add src/superlocalmemory/core/engine.py tests/test_core/test_engine_store_profile.py
git commit -m "feat(engine): per-request profile_id threading on store paths"
```

---

### Task 2: 邻接缓存 profile 键控 LRU

**Files:**
- Modify: `src/superlocalmemory/retrieval/entity_channel.py`(`_ensure_adjacency` ~250-345 及实例属性)
- Test: `tests/test_retrieval/test_adjacency_profile_lru.py`(新建)

**Interfaces:**
- Consumes: 现有 `_adj`/`_adj_profile`/`_adj_scope_key`/`_adj_edge_count`/`_adj_fact_count`/`_adj_loaded_at` 单槽状态
- Produces: profile 键控多槽缓存,对外行为零变化(`_ensure_adjacency(profile_id, ...)` 签名不变);`SLM_ADJ_CACHE_PROFILES`(默认 "3")

- [ ] **Step 1: 写失败测试**

```python
# tests/test_retrieval/test_adjacency_profile_lru.py
"""Adjacency cache: profile-keyed LRU replaces single-slot reload thrash."""
from __future__ import annotations

from unittest.mock import patch

from superlocalmemory.retrieval.entity_channel import EntityChannel


def _channel(tmp_path):
    # 按 tests/test_retrieval 现有 EntityChannel fixture 惯例构造(真 DB,mock embedder)
    ...


class TestProfileLRU:
    def test_interleaved_profiles_do_not_reload(self, tmp_path):
        ch = _channel(tmp_path)
        with patch.object(EntityChannel, "_load_adjacency_from_db",
                          wraps=ch._load_adjacency_from_db) as load:
            ch._ensure_adjacency("a", include_global=False, include_shared=False)
            ch._ensure_adjacency("b", include_global=False, include_shared=False)
            ch._ensure_adjacency("a", include_global=False, include_shared=False)  # 热命中
            ch._ensure_adjacency("b", include_global=False, include_shared=False)  # 热命中
        assert load.call_count == 2   # 单槽实现此处为 4

    def test_lru_eviction_reloads_evicted_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SLM_ADJ_CACHE_PROFILES", "2")
        ch = _channel(tmp_path)
        ch._ensure_adjacency("a", include_global=False, include_shared=False)
        ch._ensure_adjacency("b", include_global=False, include_shared=False)
        ch._ensure_adjacency("c", include_global=False, include_shared=False)  # 逐出 a
        with patch.object(EntityChannel, "_load_adjacency_from_db",
                          wraps=ch._load_adjacency_from_db) as load:
            ch._ensure_adjacency("a", include_global=False, include_shared=False)
        assert load.call_count == 1   # a 被逐出后重载
```

(注:`_load_adjacency_from_db` 为示意 mock 点——若实际加载不是独立方法,以"重载发生"的可观测信号(重载日志/计数器/DB 查询 mock)为断言点,意图不变。fixture 惯例记录进 report。)

- [ ] **Step 2: 确认失败**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/test_retrieval/test_adjacency_profile_lru.py -q`
Expected: FAIL(call_count 为 4 / 属性不存在)

- [ ] **Step 3: 实现——单槽状态改 dict[scope_key, slot] + OrderedDict LRU**

```python
# 模块级:
_ADJ_CACHE_PROFILES = int(os.environ.get("SLM_ADJ_CACHE_PROFILES", "3"))

# __init__:self._adj_slots: "OrderedDict[tuple[str, bool, bool], _AdjSlot]" = OrderedDict()
# _AdjSlot 为轻量 dataclass:adj / entity_to_facts / visible_fact_ids / edge_count /
#   fact_count / loaded_at / graph_metrics(沿用现单槽各字段的成组搬运)
# _ensure_adjacency:staleness 判定逐槽(edge_count 变化 / TTL 过期→重载该槽);
#   命中即 move_to_end;插入后超上限逐出最旧;对外读接口(self._adj 等兼容属性)
#   指向"当前调用槽"(保持下游读取代码零改动——以 property 或方法尾刷新实现,二选一在 report 记录)
```

- [ ] **Step 4: 跑测试 + entity channel 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/test_retrieval/ -q`
Expected: 全绿(含既有 entity_channel 用例——兼容属性保证下游零感知)。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/retrieval/entity_channel.py tests/test_retrieval/test_adjacency_profile_lru.py
git commit -m "perf(retrieval): profile-keyed LRU for adjacency cache (interleaved profile recall)"
```

---

### Task 3: daemon 路由(/remember + /recall + unknown_profile)

**Files:**
- Modify: `src/superlocalmemory/server/unified_daemon.py`(`/remember` ~4675、`/recall` ~4468、`RememberRequest` ~627、`_require_remember_profile` ~700)
- Test: `tests/test_server/test_per_request_profile.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `engine.store(..., profile_id=)`;既有 `engine.recall(query, profile_id=)`
- Produces(Task 4/5 消费):`POST /remember` body 增 `profile_id`(路由语义);`GET /recall?profile_id=`;错误响应 `{"success": false, "error": {"code": "unknown_profile", ...}}` + HTTP 404

- [ ] **Step 1: 写失败测试(FastAPI TestClient + 真 DB fixture,沿用 tests/test_server 现有 daemon 测试惯例)**

```python
# tests/test_server/test_per_request_profile.py
"""Per-request profile routing at the daemon layer (spec §3/§5)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def daemon(tmp_path):
    # 按 tests/test_server 现有 unified_daemon TestClient fixture 惯例构造
    # (SLM_DATA_DIR=tmp_path,两 profile:a/b 预创建);产出 (client, app)
    ...


class TestRouting:
    def test_remember_routes_to_explicit_profile(self, daemon):
        client, _ = daemon
        r = client.post("/remember", json={"content": "doris fact", "profile_id": "b"})
        assert r.status_code == 200
        # 断言:fact 落 profile b(engine._db 按 profile 查询),profile a 查不到

    def test_recall_routes_to_explicit_profile(self, daemon):
        client, _ = daemon
        client.post("/remember", json={"content": "doris fact", "profile_id": "b"})
        r = client.get("/recall", params={"q": "doris fact", "profile_id": "b"})
        assert r.status_code == 200 and r.json()["results"]   # 命中
        r2 = client.get("/recall", params={"q": "doris fact", "profile_id": "a"})
        assert r2.json()["results"] == []                     # 隔离

    def test_global_pointer_untouched(self, daemon):
        client, _ = daemon
        s0 = client.get("/status").json()
        client.post("/remember", json={"content": "x", "profile_id": "b"})
        client.get("/recall", params={"q": "x", "profile_id": "b"})
        s1 = client.get("/status").json()
        assert s1["profile"] == s0["profile"]
        assert s1["profile_generation"] == s0["profile_generation"]

    def test_unknown_profile_rejected(self, daemon):
        client, _ = daemon
        r = client.post("/remember", json={"content": "x", "profile_id": "ghost"})
        assert r.status_code == 404
        body = r.json()
        assert body["success"] is False
        assert body["error"]["code"] == "unknown_profile"
        # profiles 表行数不变(不隐式创建)

    def test_empty_profile_id_is_legacy_path(self, daemon):
        client, _ = daemon
        r = client.post("/remember", json={"content": "legacy"})
        assert r.status_code == 200
        # 断言落当前活跃 profile(与改前一致)

    def test_active_profile_explicit_is_not_error(self, daemon):
        client, _ = daemon
        r = client.post("/remember",
                        json={"content": "x", "profile_id": <当前活跃>})
        assert r.status_code == 200
```

- [ ] **Step 2: 确认失败**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/test_server/test_per_request_profile.py -q`
Expected: FAIL(profile_id 路由不存在;recall 忽略参数落到活跃 profile;unknown profile 被守卫 409 或静默落活跃)

- [ ] **Step 3: 实现**

`/remember`(在 `_require_write_actor` 之后、守卫之前):

```python
        req_profile = (req.profile_id or "").strip()
        if req_profile:
            rows = engine._db.execute(
                "SELECT 1 FROM profiles WHERE profile_id = ?", (req_profile,),
            )
            if not rows:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "unknown_profile",
                            "profile_id": req_profile},
                )
            effective_profile = req_profile       # 纯路由,绕过活跃指针
        else:
            effective_profile = None              # 现状路径(守卫逻辑位保留,对其不可达)
        # engine.store(...) 调用处追加 profile_id=effective_profile
```

`/recall`:签名加 `profile_id: str = ""` query 参数;非空走同一段存在性校验(404 同构)后 `engine.recall(..., profile_id=req_profile)`;空则现状调用。

错误体统一:路由层包成 `{"success": false, "error": {"code": "unknown_profile", "profile_id": ...}}`(沿用 daemon 现有 error envelope 惯例,report 记录采用的 envelope 形态)。

- [ ] **Step 4: 跑测试 + daemon 既有回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/test_server/ -q`
Expected: 新 6 项 PASS + 既有全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/server/unified_daemon.py tests/test_server/test_per_request_profile.py
git commit -m "feat(daemon): per-request profile routing on /remember and /recall"
```

---

### Task 4: MCP 工具面暴露

**Files:**
- Modify: `src/superlocalmemory/mcp/tools_core.py`(remember ~82、recall ~307 的签名与 daemon 调用)
- Verify(预期零改动,验证后记录):`_daemon_proxy.py`、MCP 工具 schema 校验层
- Test: 扩充 `tests/test_server/test_per_request_profile.py` 或 tests/test_mcp 现有文件

**Interfaces:**
- Consumes: Task 3 的 daemon 参数
- Produces: MCP 工具 `remember(content, ..., profile_id: str = "")` / `recall(query, ..., profile_id: str = "")`;空 = 现状

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_server/test_per_request_profile.py
class TestMcpSurface:
    def test_remember_tool_accepts_profile_id(self, mcp_server):  # 复用现有 MCP 工具测试 fixture
        result = mcp_server.call_tool("remember", {
            "content": "mcp fact", "profile_id": "b",
        })
        assert result["success"] is True
        # 断言落 b

    def test_recall_tool_accepts_profile_id(self, mcp_server):
        result = mcp_server.call_tool("recall", {
            "query": "mcp fact", "profile_id": "b",
        })
        assert result["results"]

    def test_tool_schema_allows_new_optional_param(self, mcp_server):
        schema = mcp_server.get_tool_schema("remember")
        assert "profile_id" in schema["inputSchema"]["properties"]
        assert schema["inputSchema"]["required"].count("profile_id") == 0   # 可选
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**

两工具签名各加 `profile_id: str = ""`(docstring 注明"explicit namespace anchor; empty = active profile");工具内部的 daemon 调用(POST body / GET params)与离线回落(engine 直调)透传该参数。验证 `_daemon_proxy` 与 schema 层零改动可透传(若 schema 白名单机制拦截新参数,在该层补可选参数放行,report 记录)。

- [ ] **Step 4: 跑测试 + MCP 回归**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest tests/test_mcp/ tests/test_server/test_per_request_profile.py -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/mcp/tools_core.py tests/test_server/test_per_request_profile.py
git commit -m "feat(mcp): expose optional profile_id on remember/recall tools"
```

---

### Task 5: hermes provider pin 适配

**Files:**
- Modify: `src/superlocalmemory/integrations/hermes/__init__.py`(provider 初始化 + 7 个 `_daemon_api` 调用点 + 离线回落)
- Test: `src/superlocalmemory/integrations/hermes/tests/test_provider.py`(扩充)

**Interfaces:**
- Consumes: Task 3 daemon 参数;Task 1 `engine.store(..., profile_id=)`;既有 `engine.recall(profile_id=)`
- Produces: provider 行为——`pin_profile` 默认 true:全部 recall/store 调用携带 `profile_id=self._mslm_profile`;false:省略(跟随活跃)

- [ ] **Step 1: 写失败测试(复用该文件现有 mock 惯例)**

```python
# 追加 class TestProfilePin:
def test_pin_on_sends_profile_id_on_every_daemon_call(provider, mock_daemon):
    provider._pin_profile = True
    provider._mslm_profile = "zhihui"
    provider._engine_recall(...)   # 触发 daemon 路由
    provider._engine_store(...)
    for call in mock_daemon.calls:
        assert call.profile_id == "zhihui"

def test_pin_off_omits_profile_id(provider, mock_daemon):
    provider._pin_profile = False
    provider._engine_recall(...)
    for call in mock_daemon.calls:
        assert call.profile_id in (None, "")   # 未携带

def test_offline_fallback_passes_profile_to_engine(provider, mock_engine):
    provider._pin_profile = True
    provider._mslm_profile = "zhihui"
    # daemon 不可达 → engine 直调;断言 engine.recall/store 收到 profile_id="zhihui"
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**

`__init__` 读配置(env `SLM_HERMES_PIN_PROFILE`,默认 "1";解析失败视为 true——safe default);recall 的 query string 拼 `&profile_id=<quoted>`(pin on 且 `_mslm_profile` 非空),store 的 body 加 `"profile_id"` 键;离线回落分支给 `engine.recall/store` 传 `profile_id=self._mslm_profile`。

- [ ] **Step 4: 跑 hermes 套件(既有用例若因默认 pin 变化破裂,以显式 `pin_profile=False` 适配该用例并在 report 记录——语义即"旧客户端跟随活跃指针"的活体样本)**

Run: `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src ~/miniconda3/bin/python -m pytest src/superlocalmemory/integrations/hermes/tests -q`
Expected: 全绿(92+新增)。

- [ ] **Step 5: 提交**

```bash
git add src/superlocalmemory/integrations/hermes/__init__.py src/superlocalmemory/integrations/hermes/tests/test_provider.py
git commit -m "feat(hermes): pin provider to configured profile via per-request profile_id (default on)"
```

---

### Task 6: 集成验收、并发清点、回归门与文档

**Files:**
- Test: `tests/test_integration/test_per_request_profile_e2e.py`(新建)
- Modify: `CHANGELOG.md`、`docs/superpowers/specs/2026-08-30-per-request-profile-design.md`(状态行)

**Interfaces:**
- Consumes: Tasks 1–5 全部
- Produces: 终态验收记录

- [ ] **Step 1: 端到端集成测试(真实 daemon 子进程,隔离 SLM_DATA_DIR + ephemeral 端口;形态仿 `tests/test_integration/test_embedding_fallback_two_process.py`)**

```python
# 覆盖需求书 §5 验收 1/2/3/6:
# 1) 双客户端(doris/zhihui)交错 remember/recall:各自命中、互不可见
# 2) 全程 /status 的 profile 与 profile_generation 不变
# 3) 两线程各带不同 profile 并发写 N=50:落库按 profile 分组清点,零交叉、总数==100
# 6) SLM_MCP_TOOLS=remember,recall 的 mcp 子进程携带 profile_id 全通(stdio 往返)
```

(进程编排、端口与数据根隔离的具体写法沿用 test_embedding_fallback_two_process.py 的已验证模式;测试结束恢复机器状态——生产 daemon 8765 全程不动。)

- [ ] **Step 2: 全量回归门(后台+轮询,预期 0 failed;deselect 集合 = Task 0 门禁时确定的集合)**

```bash
cd /tmp/slm-prp && env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src \
  ~/miniconda3/bin/python -m pytest tests/ -q <deselects> > /tmp/prp-final-gate.log 2>&1
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-prp/src \
  ~/miniconda3/bin/python -m pytest src/superlocalmemory/integrations/hermes/tests -q
```

- [ ] **Step 3: gitnexus 核查(无 MCP 退化:`git diff main..HEAD --stat` 应仅含 engine/entity_channel/unified_daemon/tools_core/hermes/测试/CHANGELOG/spec 状态行 + Task 0 merge)**

- [ ] **Step 4: 文档与提交**

```markdown
# CHANGELOG.md 顶部:
## mslm 4.2.0+ — per-request profile routing (2026-08-30)
- remember/recall accept an optional profile_id: the operation routes to that
  namespace with zero global-pointer side effects; omitting it is byte-for-byte
  legacy behavior. Hermes provider pins to its configured profile by default
  (SLM_HERMES_PIN_PROFILE=0 to follow the active pointer). Adjacency cache is
  now a profile-keyed LRU (SLM_ADJ_CACHE_PROFILES, default 3).
```

```bash
git add -A && git commit -m "test+docs: per-request profile e2e/concurrency acceptance and changelog"
```

---

## Self-Review 记录

- **Spec 覆盖**:§3 三层穿透→T3/T4;§4 store 穿透(含审计与 materializer 验证)→T1;§4 LRU→T2;§5 错误/并发/安全→T3 错误表+T6 并发清点(allowlist/RBAC 为"不改动"约束,由回归门兜底);§6 测试 1–8→T3(1–5)+T6(6–8 对应集成/并发/MCP stdio);§7 hermes pin→T5;§8 Task 0→T0;R4 按决策不做。
- **Placeholder 扫描**:无 TBD/TODO。两处示意性 mock 点(T1 `_store_pipeline_run`、T2 `_load_adjacency_from_db`)均附"以审计/可观测信号定真实断言点"的明确判定规则与不变意图,属实现发现步骤而非占位;fixture 复用点均指向现有惯例并要求记录。
- **类型一致性**:`profile_id: str | None = None`(engine,keyword-only)/ `profile_id: str = ""`(daemon query、MCP 工具、hermes pin)两档刻意区分(内部 None 哨兵 vs API 空串),与 spec §5 错误表一致;`error_code:"unknown_profile"`、`SLM_ADJ_CACHE_PROFILES`(默认 3)、`SLM_HERMES_PIN_PROFILE`(默认 1)全文与 spec 逐字一致。
- **风险前移**:T0 是最大不确定源(120 提交 merge),已置于首位并带完整存活核查;T1 的审计步骤把 spec §4 的"判定标准"落为可执行产出物(表格进 report)。
