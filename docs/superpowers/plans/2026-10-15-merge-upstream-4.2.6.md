# 上游 4.2.6 合并实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次性合并上游 `v4.2.6`(2045 提交)进 fork,按 spec 处置三个撞车点(护栏退役/M052→M064 重编号/版本对齐 4.2.6),fork 补丁全数存活,全量 gate 不新增红。

**Architecture:** worktree 一次性 `git merge v4.2.6`,冲突按四类策略解(取上游/上游为基+fork 参数/合流/重编号);插件资产批量取上游;lifecycle/storage/engine 三集群各自带测试循环;merge 与生产部署解耦。

**Tech Stack:** Python 3.13、SQLite、pytest、miniconda(无 .venv)。测试命令:`env -u ALL_PROXY -u all_proxy PYTHONPATH=<worktree>/src ~/miniconda3/bin/python -m pytest`。

**Spec:** `docs/superpowers/specs/2026-10-15-merge-upstream-4.2.6-design.md`(九节:撞车点/护栏退役/重编号/版本依赖/存活审计/测试生产门/文档回写/风险回滚/非目标)。读计划前必读 spec §2/§3/§5/§8。

**冲突实况(2026-10-15 探针 merge 实测):** 149 个冲突 = 102 个插件/宿主资产(`*plugin*`/`skills`/`agents/`,策略=批量取上游)+ 47 个实质冲突,后者的集群归属见各 Task。`tests/test_version_consistency.py`、`uv.lock` 之外无自动合并陷阱;`uv.lock` 冲突取上游整文件。

## Global Constraints

- **worktree:** `/tmp/slm-merge-426`,分支 `merge/upstream-4.2.6`,基于 main `53f5ed5a`;全程在此,main 只在 Task 7 合并。
- **基线红:** main 现有 16 个既有红(12 README 契约 + test_is_stale + hermes inventory + enrich×2)——gate 验收=**不新增红**;上游修复消掉的部分记录数字,不追功。
- **冲突总策略(spec §8):** `maintenance.py` 等被上游重写的文件**以上游为基底**,fork 侧只存活审计表(§5)中"未碰"项的补丁;禁止手搓混合体。
- **迁移铁律:** 上游 M052(memory_kinds)-M063 原样进;fork 旧 M052 改名 M064,production migration_log 的历史记录不动;`_schema_version=64`。
- **卫生:** 落盘文件无真实身份路径(`/home/kai-remote`)——`tests/test_repo_hygiene.py` 咬过本仓库两次。
- **AGENTS.md:** 本环境无 gitnexus MCP;每次提交前人工核对 `git diff --stat` 范围(等效 detect_changes),写入报告。
- **生产隔离:** 生产 daemon 跑 editable(main),main 合并推送后、daemon 重启前生产行为不变;部署是 Task 8(🔒 主人逐步确认,有序重启铁律:gateway 先停→daemon 重启→gateway 后起)。
- **已知 flake:** 全量 gate `-n 8` 有既有 load flake;孤立串行复验通过=不阻塞,其他新红必须停下报告。

---

### Task 1: Worktree + merge + 插件资产/元数据批量取上游

**Files:**
- Create: `/tmp/slm-merge-426`(worktree,分支 `merge/upstream-4.2.6`)
- Modify(冲突解决,策略=取上游整文件): 102 个 `*plugin*`/`skills`/`agents/` 资产 + `.gitignore`、`CITATION.cff`、`uv.lock`、`package-lock.json`
- 保留冲突不动(后续 Task 解): 47 个实质冲突中的 src/tests 文件

**Interfaces:**
- Produces: 一个未提交的 merge 状态(实质冲突仍标记 U),供 Task 2-4 逐集群解。

- [ ] **Step 1: 建 worktree 并发起 merge**

```bash
cd ~/github/superlocalmemory
git worktree add /tmp/slm-merge-426 -b merge/upstream-4.2.6 main
cd /tmp/slm-merge-426
git merge --no-commit --no-ff v4.2.6 2>&1 | tail -3   # 期望: 自动合并失败,149 冲突
```

- [ ] **Step 2: 插件资产 + 元数据批量取上游**

```bash
cd /tmp/slm-merge-426
# 插件/宿主资产:全部取上游(上游重新生成物,fork 侧只有版本号 bump,无独有内容)
git diff --name-only --diff-filter=U | grep -E "plugin|skills/|agents/" | xargs git checkout --theirs --
# 元数据取上游:uv.lock(上游重生成)、.gitignore、CITATION.cff、package-lock.json
git checkout --theirs -- uv.lock .gitignore CITATION.cff package-lock.json
git add $(git diff --name-only --diff-filter=U | grep -E "plugin|skills/|agents/" | tr '\n' ' ') uv.lock .gitignore CITATION.cff package-lock.json 2>/dev/null || git add -A ':!*superlocalmemory*' 2>/dev/null
git status --porcelain | grep -c "^UU"   # 期望: 剩 43 个左右实质冲突(149-102-4)
```

- [ ] **Step 3: 记录剩余冲突清单到报告,不提交**

```bash
git diff --name-only --diff-filter=U | sort > /tmp/slm-merge-426/.merge-conflicts-remaining.txt
wc -l /tmp/slm-merge-426/.merge-conflicts-remaining.txt
```

merge 保持未提交状态跨 Task(各 Task 解完自己集群 `git add` 对应文件;最后由 Task 5 收口提交)。`.merge-conflicts-remaining.txt` 加进 `.git/info/exclude`,不进提交。

---

### Task 2: Lifecycle 集群——护栏退役 + M052→M064 重编号

**Files:**
- Resolve(取上游整文件): `src/superlocalmemory/core/maintenance.py`
- Rename+Modify: `src/superlocalmemory/storage/migrations/M052_provenance_kind_column.py` → `M064_provenance_kind_column.py`
- Modify: `src/superlocalmemory/storage/migration_runner.py`(目录登记)、`src/superlocalmemory/storage/_migration_internals.py`(MODULES)、`src/superlocalmemory/storage/_schema_version.py`(64)
- Rewrite: `tests/test_core/test_lifecycle_guard.py`(退役护栏单测,保留改编的行为回归)
- Resolve(取上游+名称替换): `tests/test_migrations/test_backward_compat_405.py`
- 既有上游新文件(merge 自带入,验证即可): `src/superlocalmemory/core/langevin_tier.py`、`tests/test_maintenance_never_archives_a_used_memory.py`(路径以上游为准)

**Interfaces:**
- Produces: M064 迁移(`NAME = "M064_provenance_kind_column"`,依赖 `(M046,)`,PRAGMA 列守卫幂等);`SUPPORTED_SCHEMA_VERSION = 64`;`test_lifecycle_guard.py` 改编版(下述);maintenance.py = 上游形态(无 fork 改动)。

- [ ] **Step 1: maintenance.py 取上游整文件**

```bash
cd /tmp/slm-merge-426
git checkout --theirs -- src/superlocalmemory/core/maintenance.py
git add src/superlocalmemory/core/maintenance.py
grep -c "_guard_zone_updates\|langevin_guard_refused" src/superlocalmemory/core/maintenance.py   # 期望 0
grep -c "_langevin_pass" src/superlocalmemory/core/maintenance.py   # 期望 >0(上游方案在位)
```

- [ ] **Step 2: M052 → M064 重编号**

```bash
cd /tmp/slm-merge-426
git mv src/superlocalmemory/storage/migrations/M052_provenance_kind_column.py \
       src/superlocalmemory/storage/migrations/M064_provenance_kind_column.py
```

`M064_provenance_kind_column.py` 内 `NAME = "M052_provenance_kind_column"` → `NAME = "M064_provenance_kind_column"`;docstring 若提及编号一并改。

`migration_runner.py`(已解冲突到可编辑状态后)在 M063 登记之后追加:

```python
from superlocalmemory.storage.migrations import (
    M064_provenance_kind_column as _M064,
)
```

DEFERRED_MIGRATIONS 列表末尾(M052 上游条目之后,保持 fork 迁移在最后):

```python
    # Fork addition: governance gating tag (controlled vocabulary, NULL =
    # not yet tagged). Renumbered from fork's historical M052 after upstream
    # took M052-M063. Idempotent on stores that applied the old name: the
    # PRAGMA column guard makes re-application a no-op that only records.
    Migration(name=_M064.NAME, db_target="memory", ddl=_M064.DDL,
              dependencies=(_M046.NAME,)),
```

`_migration_internals.py` 的 `_MODULES` 注册同步改名。`_schema_version.py`:

```python
SUPPORTED_SCHEMA_VERSION: int = 64
```

`tests/test_migrations/test_backward_compat_405.py` 解冲突(取上游为基),把其中 fork 侧引用 `M052_provenance_kind_column` 处替换为 `M064_provenance_kind_column`(该测试验证旧库升级路径,fork 侧仅迁移名引用)。

- [ ] **Step 3: test_lifecycle_guard.py 改编(删护栏单测,留行为回归)**

整文件替换为:

```python
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The repair sequence the fork ran in production, pinned against the
upstream implementation that replaced it.

HISTORY. The fork shipped a score guard on 2026-09-25 (运维笔记 §19/§21):
the maintenance Langevin chain derived zones from position radius alone and
saturated positions archived healthy memories nightly. Upstream fixed the
same defect at the root in 4.1.18 (74cb08b8): every pass now REMEASURES the
radius as ``1 - R(t)`` (``core/langevin_tier.py``), so positions cannot
drift and the guard retired. Three weeks of production held archive at 0.

What this file still pins — the guarantees the repair must keep under the
upstream implementation:
  - a saturated legacy position cannot keep a used memory archived: the
    next pass remeasures the radius and the tier follows the score;
  - M051 re-runnability (the fork's runbook relies on apply() twice);
  - the full production repair sequence (maintenance -> M051 -> M043 ->
    reseed) converges with zones held.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.schema import create_all_tables
from superlocalmemory.core.maintenance import run_maintenance

_PROFILE = "default"
_SATURATED = [0.35] * 8   # norm 0.9899 — the production signature (§19)


def _cfg() -> SLMConfig:
    cfg = SLMConfig()
    cfg.math.sheaf_at_encoding = False
    cfg.math.fisher_bayesian_update = False
    cfg.math.ebbinghaus_langevin_coupling_enabled = False
    return cfg


def _insert_fact(conn, fact_id, *, lifecycle="active", position=None,
                 age_days=60, access_count=0):
    conn.execute(
        "INSERT OR IGNORE INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', ?, 'source')", (_PROFILE,),
    )
    created = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
        " lifecycle, langevin_position, access_count, scope, created_at)"
        " VALUES (?, 'm1', ?, ?, ?, ?, ?, 'global', ?)",
        (fact_id, _PROFILE, f"content {fact_id}", lifecycle,
         json.dumps(position) if position is not None else None,
         access_count, created),
    )


def _insert_retention(conn, fact_id, *, zone, score):
    conn.execute(
        "INSERT INTO fact_retention (fact_id, profile_id, lifecycle_zone,"
        " retention_score) VALUES (?, ?, ?, ?)",
        (fact_id, _PROFILE, zone, score),
    )


def _zone(db, fact_id):
    rows = db.execute(
        "SELECT lifecycle_zone FROM fact_retention WHERE fact_id=?", (fact_id,))
    return str(dict(rows[0])["lifecycle_zone"]) if rows else None


def _radius(db, fact_id):
    rows = db.execute(
        "SELECT langevin_position FROM atomic_facts WHERE fact_id=?",
        (fact_id,),
    )
    raw = dict(rows[0])["langevin_position"]
    pos = json.loads(raw) if raw else None
    return sum(x * x for x in pos) ** 0.5 if pos else None


@pytest.fixture()
def store(tmp_path: Path) -> DatabaseManager:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    create_all_tables(conn)
    conn.commit()
    conn.close()
    return DatabaseManager(str(tmp_path / "memory.db"))


class TestSaturatedLegacyPositionCannotArchiveAUsedMemory:
    def test_remeasure_frees_the_tier_and_the_radius(self, store, tmp_path):
        """§19's production shape: saturated position, score 0.9. Under the
        upstream pass the radius is re-derived from retention, so the tier
        returns to what the score says and the radius leaves the boundary."""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="archived", position=_SATURATED,
                     access_count=8)
        _insert_retention(conn, "f1", zone="archive", score=0.9)
        conn.commit()
        conn.close()

        run_maintenance(store, _cfg(), _PROFILE)

        assert _zone(store, "f1") in {"active", "warm"}, (
            "a memory whose retention keeps it must not stay archived"
        )
        r = _radius(store, "f1")
        assert r is not None and r < 0.80, (
            f"the saturated radius must be re-measured below the archive "
            f"boundary, got {r}"
        )


class TestM051Rerun:
    def test_apply_twice_then_reseed_once(self, tmp_path):
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        create_all_tables(conn)
        _insert_fact(conn, "f1", lifecycle="archived", position=_SATURATED)
        _insert_fact(conn, "f2", lifecycle="archived", position=_SATURATED)
        conn.commit()

        m051.apply(conn)
        conn.commit()
        assert all(
            dict(r)["langevin_position"] is None
            for r in conn.execute(
                "SELECT langevin_position FROM atomic_facts")
        )
        m051.apply(conn)
        conn.commit()
        conn.close()

        db = DatabaseManager(str(path))
        counts1 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts1["langevin_backfilled"] == 2
        counts2 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts2["langevin_backfilled"] == 0
        db.close()


class TestProductionReplay:
    def test_saturated_store_repair_sequence(self, tmp_path):
        from superlocalmemory.storage.migrations import (
            M043_quarantine_display_summaries as m043,
        )
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
        create_all_tables(conn)
        _insert_fact(conn, "g1", lifecycle="active", position=_SATURATED)
        _insert_fact(conn, "g2", lifecycle="active", position=_SATURATED)
        _insert_retention(conn, "g1", zone="active", score=1.0)
        _insert_retention(conn, "g2", zone="active", score=1.0)
        _insert_fact(conn, "p1", lifecycle="archived", position=_SATURATED)
        _insert_retention(conn, "p1", zone="archive", score=1.0)
        conn.commit()

        db = DatabaseManager(str(path))
        run_maintenance(db, _cfg(), _PROFILE)
        db.close()

        conn = sqlite3.connect(str(path))
        m051.apply(conn)
        conn.commit()
        m043.apply(conn)
        conn.close()

        db = DatabaseManager(str(path))
        assert _zone(db, "p1") == "active"
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 3
        for fid in ("g1", "g2", "p1"):
            assert _zone(db, fid) in {"active", "warm"}
            assert _radius(db, fid) is not None
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 0
        db.close()
```

- [ ] **Step 4: 集群测试循环**

```bash
cd /tmp/slm-merge-426
git add src/superlocalmemory/core/maintenance.py \
  src/superlocalmemory/storage/migrations/M064_provenance_kind_column.py \
  src/superlocalmemory/storage/migration_runner.py \
  src/superlocalmemory/storage/_migration_internals.py \
  src/superlocalmemory/storage/_schema_version.py \
  tests/test_core/test_lifecycle_guard.py tests/test_migrations/
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-merge-426/src ~/miniconda3/bin/python -m pytest \
  tests/test_core/test_lifecycle_guard.py tests/test_migrations/ \
  tests/test_maintenance_never_archives_a_used_memory.py \
  tests/test_storage/test_migration_dependency_and_version.py \
  tests/test_storage/test_provenance_kind.py \
  tests/test_storage/test_migrations.py tests/test_storage/test_migration_runner.py \
  -q
```

预期:全 PASS。上游自带的 `test_maintenance_never_archives_a_used_memory.py` 必须绿(护栏退役后的行为背书);版本一致性测试(上限=64=最大序号)必须绿。

---

### Task 3: Storage 集群——provenance 过滤/模型/correction 合流/WAL

**Files:**
- Resolve(上游为基+fork 参数): `src/superlocalmemory/storage/database.py`
- Resolve(合流): `src/superlocalmemory/storage/models.py`(fork provenance_kind 字段+词表 vs 上游 memory_kinds 列)
- Resolve(合流): `src/superlocalmemory/storage/correction_cases.py`(fork CorrectionPredecessorBusyError 幂等 vs 上游 review-gate)
- Resolve: `src/superlocalmemory/storage/write_coordinator.py`、`tests/test_storage/test_no_ckpt_on_close.py`、`tests/test_storage/test_single_writer_lock.py`(fork WAL 修复 vs 上游演化)、`src/superlocalmemory/storage/admission_journal.py`、`src/superlocalmemory/storage/agent_experience.py`
- Resolve(取上游+名称替换): `tests/test_migrations/test_backward_compat_405.py` 若 Task 2 未尽

**Interfaces:**
- Consumes: Task 2 的 M064/模型词表。
- Produces: `get_all_facts(..., scope, provenance_kind, provenance_kind_null)` 在上游召回重构结构中存活;`CorrectionPredecessorBusyError` 在上游 review-gate 结构中存活;WAL 行为(fork 修复)不回退。

- [ ] **Step 1: database.py——上游结构 + fork 过滤参数**

解冲突原则:上游的召回预算/排队/缓存改动全部保留;fork 侧只保 `get_all_facts` 的三个过滤参数 + WHERE 拼接块 + `_UPDATABLE_FACT_COLUMNS` 的 `provenance_kind`。逐 hunk 检查:上游若重构了 `get_all_facts` 签名/查询形状,把 fork 三参数与过滤块移植进新形状;若上游动了 `_row_to_fact`,确认 `provenance_kind` 列仍被读出。

- [ ] **Step 2: models.py——两边列族并存**

fork 侧保留:`PROVENANCE_KINDS`/`validate_provenance_kind`/`parse_scan_filters`/`VALID_SCOPES`/`AtomicFact.provenance_kind`。上游 memory_kinds 五列及其模型面全收。冲突 hunk 大概率只在 `AtomicFact` dataclass 字段区与文件头 import——两边字段并排,不互斥。

- [ ] **Step 3: correction_cases.py——幂等合流进 review-gate**

先读上游版本全文,理解 review-gate(d489f2a4/bf1ecfa5/ea373853)结构;fork 的 `CorrectionPredecessorBusyError` 与"同 successor 幂等返回/异 successor 类型化抛出"守卫**必须存活**——若上游已有等价机制,保上游并删除 fork 重复;若没有,把 fork 守卫移植进上游新结构。store_pipeline 侧调用点一并核对。

- [ ] **Step 4: WAL 三件套**

`write_coordinator.py`/`test_no_ckpt_on_close.py`/`test_single_writer_lock.py`:对照 fork 修复意图(NO_CKPT_ON_CLOSE on per-call connections、busy-timeout ordering),上游演化若已覆盖则取上游,否则保 fork 补丁在上游新结构中。

- [ ] **Step 5: 集群测试循环**

```bash
cd /tmp/slm-merge-426
git add src/superlocalmemory/storage/ tests/test_storage/
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-merge-426/src ~/miniconda3/bin/python -m pytest \
  tests/test_storage/ -q -x --ignore=tests/test_storage/test_backfill_missing_embeddings.py
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-merge-426/src ~/miniconda3/bin/python -m pytest \
  tests/test_storage/test_backfill_missing_embeddings.py tests/test_core/test_correction_candidate_pipeline.py \
  $(ls tests/test_*correction* tests/test_storage/test_correction* 2>/dev/null | tr '\n' ' ') -q 2>/dev/null || true
```

预期:storage 群全 PASS(基线红不含 storage);correction 相关全 PASS。

---

### Task 4: Engine/Embedding/Learning/MCP/Server 集群

**Files:**
- Resolve(逐文件,同"上游为基+fork 补丁移植"原则): `core/embeddings.py`、`core/mcp_embedder_proxy.py`、`core/engine_wiring.py`(EmbeddingService daemon fallback 三件套)、`core/engine.py`、`core/engine_ingestion.py`、`core/store_pipeline.py`、`core/context_cache.py`、`core/mutations.py`、`cli/pending_store.py`、`code_graph/database.py`、`compliance/audit.py`、`learning/`(bandit/database/engagement/feedback/signal_worker/signals/source_quality 七文件)、`mcp/_daemon_proxy.py`、`mcp/tools_core.py`、`mesh/outbox_remote.py`、`server/routes/memories.py`、`server/unified_daemon.py`、`tests/test_server/test_canonical_remember_route.py`、`src/superlocalmemory/__init__.py`(版本串,取 4.2.6)

**Interfaces:**
- Consumes: Task 2/3 的 storage 面。
- Produces: fork 补丁存活清单执行结果(下表逐项勾)。

- [ ] **Step 1: 逐文件解冲突,按下表登记处置**

| 文件 | fork 补丁存活点 | 上游演化 | 处置 |
|---|---|---|---|
| embeddings/mcp_embedder_proxy/engine_wiring | `_can_serve`/`_daemon_fallback`/tri-state | EmbeddingGemma2/hub baseline | fallback 语义移植进上游新结构 |
| engine/engine_ingestion/store_pipeline | per-request profile 写路径(#127 上游已收) | 召回排队/预算 | 以上游为准,profile 语义靠测试群验证 |
| unified_daemon | hermes daemon-first 路由(f4788225) | 端点演化 | 路由逻辑移植,`updated` 死键顺手对齐 `langevin_updated`(登记,不扩面) |
| learning/* | (无 fork 独有,冲突源于上游重构 × fork 历史触碰) | 大重构 | 取上游,跑测试群 |
| 其余 | 无独有补丁则取上游 | — | `git checkout --theirs` |

- [ ] **Step 2: 集群测试循环(per-request profile + hermes + 召回面)**

```bash
cd /tmp/slm-merge-426
git add -A src/ tests/ 2>/dev/null; git status --porcelain | grep -c "^UU"   # 期望 0(全部解完)
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-merge-426/src ~/miniconda3/bin/python -m pytest \
  tests/test_server/ tests/test_mcp/ tests/mcp/ tests/test_api/ \
  tests/test_per_request_profile.py tests/test_engine_recall_path.py \
  $(ls tests/*per_request* tests/test_server/*profile* 2>/dev/null | tr '\n' ' ') \
  -q 2>/dev/null | tail -3
```

预期:除基线红(hermes inventory 1 个在列)外全 PASS。

---

### Task 5: 版本 sweep + 依赖安装 + 冒烟 + merge 提交

**Files:**
- Resolve: `pyproject.toml`(版本 4.2.6 + keyring/transformers/torch/sentence-transformers 钉版,fork 的 package-data 保留)、`package.json`、`README.md`(上游重写为基 + fork 小节≤10 行)、`CHANGELOG.md`(取上游,fork 条目若被冲掉在上游条目之上补一行)
- Modify: fork 侧版本引用文件(上次 sweep 同批:`CLAUDE.md.fragment` 等,`grep -rn "4\.2\.0" --include="*.md" --include="*.json" --include="*.py" -l` 圈定)

**Interfaces:**
- Produces: 一个干净可提交的 merge 状态;miniconda 环境新依赖就位。

- [ ] **Step 1: 版本/依赖文件解冲突**(pyproject: 上游为基,保 fork `contracts/schemas/*.json` package-data 与 jsonschema 钉版;README/CHANGELOG 按上述)

- [ ] **Step 2: miniconda editable 重装 + import 冒烟**

```bash
env -u ALL_PROXY -u all_proxy ~/miniconda3/bin/python -m pip install -e /tmp/slm-merge-426 --no-deps
env -u ALL_PROXY -u all_proxy ~/miniconda3/bin/python -m pip install "keyring==25.7.0" "sentence-transformers==5.6.1" "transformers==5.10.4" 2>&1 | tail -2
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-merge-426/src ~/miniconda3/bin/python -c "
import superlocalmemory, keyring, sentence_transformers, transformers
print(superlocalmemory.__version__ if hasattr(superlocalmemory,'__version__') else 'n/a')
print('keyring', keyring.__version__, '| st', sentence_transformers.__version__, '| tf', transformers.__version__)
"
```

(torch==2.13.0 若与现环境 CUDA/轮子冲突,记录并报告——测试用 CPU 语义,钉版差异不阻塞 gate,但需登记。)

- [ ] **Step 3: 提交 merge**

```bash
cd /tmp/slm-merge-426
git add -A && git status --porcelain | head -3   # 期望干净
git commit -m "merge: upstream v4.2.6 (2045 commits) — guard retired for upstream langevin_tier, M052→M064, version 4.2.6, fork patches surviving (hermes routing / lock tolerance / ELC clock / provenance / profile routing)"
git log --oneline -1 && git diff --stat HEAD~1..HEAD | tail -3
```

---

### Task 6: 全量 gate + 基线对照 + 存活审计矩阵

**Files:** 无代码改动;产出 gate 报告。

- [ ] **Step 1: 全量 gate**

```bash
cd /tmp/slm-merge-426
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-merge-426/src ~/miniconda3/bin/python -m pytest tests/ -q -n 8 2>&1 | tail -5
```

- [ ] **Step 2: 基线对照**

失败清单与 main 16 红基线逐名比对:基线内=通过;基线外新红=停下报告,不得放行;基线红被上游修掉=记录数字。

- [ ] **Step 3: 存活审计矩阵执行(spec §5 表逐行)**

每行跑对应测试群并记录 PASS/FAIL:hermes daemon-first(hermes 测试群)、temporal 锁容忍(admission/temporal 群)、ELC 时钟(test_ebbinghaus_langevin + decay 群)、provenance 全链(test_provenance_kind + MCP scan 群)、correction(correction 群)、vec0(code_graph vec0 群)、per-request profile(profile 群)。任一 FAIL=该补丁未存活,回到对应 Task 修复。

- [ ] **Step 4: 卫生 + 报告**

```bash
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-merge-426/src ~/miniconda3/bin/python -m pytest tests/test_repo_hygiene.py -q
```

---

### Task 7: 合并 main + 推送 + 文档回写

**(合并 main 前向主人确认——外部副作用)**

- [ ] **Step 1: 合并推送**

```bash
cd ~/github/superlocalmemory
git merge --no-ff merge/upstream-4.2.6 -m "merge: upstream v4.2.6 into main"
git push origin main
git worktree remove /tmp/slm-merge-426 && git branch -d merge/upstream-4.2.6
```

- [ ] **Step 2: 上游贡献清单回写**

`docs/上游贡献清单.md`:P1 → "上游已修(74cb08b8 langevin_tier,不同方案;fork 护栏 9/25 先行生产验证,三周判别通过,运维笔记 §21)";P2(ELC 时钟)标注"merge 后仍为 fork 补丁,机会存活,提交前按 4.2.6 重整材料";P3(correction 幂等)标注"上游 review-gate 演化,新颖性待查(d489f2a4 对照)";新增 `updated` 死键小 PR 候选。commit+push。

---

### Task 8: 生产部署(🔒 主人逐步确认,非 subagent)

- [ ] **Step 1: 🔒 停 gateway(主人侧)→ 停 daemon,验端口/PID
- [ ] **Step 2: 🔒 备份 memory.db(pre-4.2.6-deploy)
- [ ] **Step 3: 🔒 起 daemon(miniconda editable 已在 Task 5 指向 worktree——**部署前先确认 editable 是否需重指 main checkout**,`pip show superlocalmemory | grep Editable`;若指向已删 worktree 则重装 `pip install -e ~/github/superlocalmemory --no-deps`)
- [ ] **Step 4: 🔒 验收**:health 全绿;迁移日志 M052_M053…M063 complete + M064 幂等空跑(旧 M052_provenance 记录仍在);首轮维护日志=上游格式(无 guard-refused 字段,backfill/updated 数字正常);zone 分布不倒退(archived 不回升);三发探针(米家日常黄金三条/迷雾小镇/Fuse)
- [ ] **Step 5: 🔒 起 gateway(zhihui 侧)→ 桥侧复核
- [ ] **Step 6: 运维笔记 §22 merge 回执**(撞车点处置/gate 数字/部署验收/迁移日志;§21 补"护栏已由上游方案接棒退役"注记),commit+push

**回滚预案:** 任一步异常 → 停 daemon → 恢复 Step 2 备份(删 -wal/-shm 后 cp 回)→ `git reset --hard 53f5ed5a^..` 不需要(代码回滚=main revert merge commit,生产数据回滚=备份恢复,两者独立)→ 运维笔记记录中止点。

---

## Self-Review 记录(计划落盘前已执行)

- **Spec 覆盖:** §2 护栏退役→Task 2 Step 1/3;§3 重编号→Task 2 Step 2;§4 版本依赖→Task 5;§5 存活审计→Task 3/4 处置 + Task 6 Step 3 矩阵;§6 测试生产门→Task 6/8;§7 文档回写→Task 7 Step 2/Task 8 Step 6。§8 风险(maintenance.py 以上游为基)已进 Global Constraints 与 Task 2 Step 1。
- **占位符扫描:** Task 3 Step 1-4 与 Task 4 为冲突解决指令而非代码模板——resolution 原则、存活点、验证命令均已具体化;无 TBD。
- **类型一致性:** M064 名称/依赖(46)/上限 64/测试引用四处一致;test_lifecycle_guard.py 改编版仅依赖上游接口(run_maintenance/M051/M043),无护栏符号残留。
- **实测锚点:** 冲突清单来自 2026-10-15 探针 merge(149=102 资产+47 实质),非推测。
