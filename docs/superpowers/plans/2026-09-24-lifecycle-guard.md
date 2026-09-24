# 生命周期护栏(Lifecycle Guard)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给维护层 Langevin 链的 `radius→zone` 写入口加分数护栏——半径可影响检索权重,但永远不能推翻 retention_score 的 zone;然后按序补完数据闭环:M051 重跑(清饱和 position)→ M043 恢复(分数信号提回误归档行)。

**Architecture:** 护栏落在 `core/maintenance.py` 三个半径写入口(step 1a 补种、step 1b batch_step、Fisher 耦合重步进),共用同一比较函数 `_is_colder` 与入口 `_guard_zone_updates`;`current_zones` 以 `fact_retention.lifecycle_zone` 为权威、`atomic_facts.lifecycle` 镜像兜底。`_persist_lifecycle` 扩展接受 `lifecycle=None` 表"只写 position,不动 zone"。ELC 写入口(maintenance.py:709)是分数域自家写者,不受护栏约束。数据修复(②③)是生产窗口内的人工操作,直接调用两个迁移模块的可重入 `apply()`,不动 migration_log。

**Tech Stack:** Python 3.13、SQLite、pytest。所有 pytest:`env -u ALL_PROXY -u all_proxy PYTHONPATH=<worktree>/src ~/miniconda3/bin/python -m pytest`(PYTHONPATH 前置;无 .venv)。

**Spec:** `docs/superpowers/specs/2026-09-24-lifecycle-guard-design.md`(权威模型 / 四步顺序 / 测试清单 1-8 / 上游 P1 叙事)。本计划逐条实现该 spec;读计划前必读 spec §3-§5。

**Spec 符合性说明(两处有意识地细化,不改设计意图):**

1. spec §4 ① 指名 `:468-469` 与 `:552` 两个 `get_lifecycle_state` 调用点。代码里还有第三个半径写入口——step 1b 的 `batch_step`(:507,zone 在 `langevin.batch_step` 内部算好)。它是生产上每 33 分钟全量重写 zone 的主力写者,不拦它等于没修。本计划在**三个**半径写入口都上护栏,与 spec "半径永不推翻分数" 的不等式一致。
2. spec §4 ④ "补种不覆盖" 在代码里已存在(maintenance.py:452 `if f.langevin_position is not None: continue`)。④ 的落地形式因此是:**回归测试钉死 + 注释钉死 + runbook 顺序保证**(清扫与补种在同一维护窗口内完成),不新增生产代码分支。

## Global Constraints

- **worktree:** `/tmp/slm-lcguard`,分支 `feat/lifecycle-guard`,基于 main(`7d2b3d4d`)。Task 1/2 在此进行,合并 main 后才进 Task 3 生产窗口。
- **测试命令:** `env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lcguard/src ~/miniconda3/bin/python -m pytest <路径>`。
- **兼容锚点:** `run_maintenance(db, config, profile_id, embedder)` 签名不变;`counts` 只增 key 不改既有 key;`_persist_lifecycle` 三元组旧调用行为字节不变;`math/langevin.py`、`math/ebbinghaus.py`、`core/lifecycle_state.py`、`learning/forgetting_scheduler.py` **零改动**。
- **护栏语义:** 允许一切升温,拒绝任何半径驱动的降温;同 zone 是跳过的 no-op。权威缺失/不可读时放行 proposed(护栏拦的是"已知更差的覆写",不是冻结写者)。
- **爆炸半径(人工核对,等效 gitnexus impact;本环境无 gitnexus MCP):** `_persist_lifecycle` 全部调用方 = maintenance.py 4 处 + 2 个测试文件,改动是 additive;`run_maintenance` 签名不变;三个新 helper 无既有调用方。提交前按 AGENTS.md 人工核对 diff 范围(替代 detect_changes)。
- **文档卫生:** 所有落盘文件不得出现真实身份绝对路径(用 `~`/`$HOME`/`<worktree>`)——`test_no_tracked_file_leaks_a_real_identity_in_a_path` 咬过本仓库两次。
- **生产操作确认点:** Task 3 每一步执行前需主人确认;有序重启铁律 = gateway 先停、daemon 后停;恢复时 daemon 先起、gateway 后起(锁竞争规避,§17 已验证流程)。
- **顺序铁律(spec §4):** ① 护栏代码先合并部署 → ② M051 重跑 → ③ M043 恢复 → ④ 防线(随 ① 的测试)。②③ 不得反序,不得跳过上一步直接恢复。

---

### Task 1: 护栏代码——比较函数、zone 图、三写入口接线

**Files:**
- Modify: `src/superlocalmemory/core/maintenance.py`(新增 `_ZONE_COLDNESS`/`_is_colder`/`_current_zone_map`/`_guard_zone_updates`;`_persist_lifecycle` 扩展;step 1a/1b/Fisher 三处接线;counts 与日志)
- Test: `tests/test_core/test_lifecycle_guard.py`(新建;本计划所有 8 个测试的唯一居所)

**Interfaces:**
- Consumes: 既有 `db.get_all_facts(profile_id)`(返回带 `.lifecycle`/`.langevin_position` 的 AtomicFact);`db.execute(sql, params)`;`_persist_lifecycle(db, profile_id, updates)`;`set_fact_lifecycle_zone`(不动)。
- Produces(Task 2 消费):
  - `_is_colder(proposed: str | None, current: str | None) -> bool` — 全函数,不可识别输入返回 False。
  - `_guard_zone_updates(updates: list[tuple[str, str, object]], current_zones: dict[str, str]) -> tuple[list[tuple[str, str | None, object]], int]` — 返回 (guarded, refused);guarded 中 zone=None 表"只写 position"。
  - `_current_zone_map(db, profile_id: str, facts: list) -> dict[str, str]` — 权威覆盖镜像,只收录可识别 zone。
  - `_persist_lifecycle` 接受 `lifecycle=None`(只写 position,跳过 zone 写)。
  - `counts["langevin_guard_refused"]: int` — 本轮护栏拒写计数,进完成日志。

- [ ] **Step 1: 建 worktree**

```bash
cd ~/github/superlocalmemory
git worktree add /tmp/slm-lcguard -b feat/lifecycle-guard main
cd /tmp/slm-lcguard
```

- [ ] **Step 2: 写失败测试(单元,spec 测试 1/4/5 + persist 扩展)**

新建 `tests/test_core/test_lifecycle_guard.py`:

```python
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The Langevin radius may propose, the retention score decides.

WHAT PRODUCTION SHOWED (运维笔记 §19). The maintenance Langevin chain writes
``lifecycle`` from ``radius -> weight -> zone`` without ever reading the
retention score. Positions drift outward nightly and saturate at 0.99; once
saturated, every fact's zone flips to archived on the next pass regardless of
a perfect score. Measured 2026-09-24: 3,306 archived rows whose zone agreed
with the radius 100% of the time and whose retention score still read 1.0.

THE GUARD. A radius-driven write may warm a fact freely, but it may never
cool one past the zone the score authority (``fact_retention.lifecycle_zone``,
written by the decay cycle's ``batch_upsert_retention``) last recorded.
Legitimate cooling still happens — through the decay cycle, which runs in the
same daemon tick (scheduler_interval_minutes=30), at most one tick later.

This file pins the guard (unit) and the production repair sequence
(integration): M051 re-run clears saturated positions, M043 restores the
rows the radius convicted, and the seeder never overwrites a position.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.lifecycle_state import set_fact_lifecycle_zone
from superlocalmemory.core.maintenance import (
    _guard_zone_updates,
    _is_colder,
    _persist_lifecycle,
    run_maintenance,
)
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.schema import create_all_tables

_PROFILE = "default"


class TestIsColder:
    @pytest.mark.parametrize(
        "proposed,current,expected",
        [
            ("archived", "active", True),
            ("archive", "warm", True),        # retention spelling
            ("forgotten", "archived", True),
            ("cold", "warm", True),
            ("warm", "cold", False),          # warming is never colder
            ("active", "archived", False),
            ("warm", "warm", False),          # equal is not colder
            ("archived", "archive", False),   # spellings share a rank
            ("nonsense", "active", False),    # unreadable input never blocks
            ("archived", "nonsense", False),
            (None, "active", False),
            ("archived", None, False),
        ],
    )
    def test_direction(self, proposed, current, expected) -> None:
        assert _is_colder(proposed, current) is expected


class TestGuardZoneUpdates:
    def test_colder_is_refused_but_the_position_survives(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "archived", [0.35] * 8)], {"f1": "active"},
        )
        assert refused == 1
        assert guarded == [("f1", None, [0.35] * 8)]

    def test_warmer_is_written(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "active", [0.05] * 8)], {"f1": "cold"},
        )
        assert refused == 0
        assert guarded == [("f1", "active", [0.05] * 8)]

    def test_same_zone_is_a_skipped_noop(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "warm", None)], {"f1": "warm"},
        )
        assert refused == 0
        assert guarded == [("f1", None, None)]

    def test_no_authority_record_writes_proposed(self) -> None:
        guarded, refused = _guard_zone_updates([("f1", "cold", None)], {})
        assert refused == 0
        assert guarded == [("f1", "cold", None)]

    def test_spelling_variants_share_a_rank(self) -> None:
        guarded, refused = _guard_zone_updates(
            [("f1", "archived", None)], {"f1": "archive"},
        )
        assert refused == 0
        assert guarded == [("f1", None, None)]


class TestEveryRadiusWriteSiteIsGuarded:
    def test_all_radius_sites_pass_through_the_guard(self) -> None:
        """AST, not grep: a comment mentioning the pattern is not the pattern.

        Four ``_persist_lifecycle`` calls live in run_maintenance. The three
        radius-driven ones (seed, batch step, Fisher re-step) must persist the
        GUARDED updates. The ELC one is deliberately raw: its zone comes from
        the retention score, which IS the authority, and it passes an inline
        list whose position element is None.
        """
        import ast
        import inspect

        from superlocalmemory.core import maintenance

        tree = ast.parse(inspect.getsource(maintenance.run_maintenance))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_persist_lifecycle"
        ]
        assert len(calls) == 4, f"expected 4 write sites, found {len(calls)}"
        for call in calls:
            updates_arg = call.args[2]
            if isinstance(updates_arg, ast.Name):
                assert updates_arg.id == "guarded", (
                    f"line {call.lineno}: radius write site must persist "
                    "the guarded updates"
                )
                continue
            # The one deliberately unguarded site is ELC: its zone comes
            # from the retention score, which IS the authority. Recognise
            # it by its inline single-row list whose position is None.
            is_elc = (
                isinstance(updates_arg, ast.List)
                and len(updates_arg.elts) == 1
                and isinstance(updates_arg.elts[0], ast.Tuple)
                and len(updates_arg.elts[0].elts) == 3
                and isinstance(updates_arg.elts[0].elts[2], ast.Constant)
                and updates_arg.elts[0].elts[2].value is None
            )
            assert is_elc, (
                f"line {call.lineno}: unguarded _persist_lifecycle outside "
                "the ELC site — radius-driven writes must go through "
                "_guard_zone_updates first"
            )
```

- [ ] **Step 3: 跑测试确认失败**

```bash
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lcguard/src ~/miniconda3/bin/python -m pytest /tmp/slm-lcguard/tests/test_core/test_lifecycle_guard.py -x -q
```

预期:FAIL,`ImportError: cannot import name '_guard_zone_updates'`(helpers 尚不存在)。

- [ ] **Step 4: 实现 helpers + `_persist_lifecycle` 扩展**

`src/superlocalmemory/core/maintenance.py`,加在 `_seed_langevin_position` 之后:

```python
# ---------------------------------------------------------------------------
# Lifecycle guard (GitHub #136 follow-up, 运维笔记 §19)
# ---------------------------------------------------------------------------
#
# Two writers touch the zone. The score authority (the decay cycle's
# batch_upsert_retention) derives it from retention_score. The radius domain
# (the three Langevin write sites below) derives it from position. Positions
# saturate at the boundary and then the radius convicts every fact to
# archived, whatever the score says -- production measured 3,306 such rows
# whose score still read 1.0. The rule from here on: the radius may warm
# freely, it may never cool past the score authority's zone. Cooling still
# happens, through the decay cycle, at most one tick later.

# Both spellings, one rank: atomic_facts says 'archived', fact_retention says
# 'archive'.
_ZONE_COLDNESS: dict[str, int] = {
    "active": 0, "warm": 1, "cold": 2,
    "archive": 3, "archived": 3, "forgotten": 4,
}


def _is_colder(proposed: str | None, current: str | None) -> bool:
    """True when ``proposed`` is a strictly colder zone than ``current``.

    Total function: an unrecognized zone on either side answers False. The
    guard exists to stop a KNOWN-worse overwrite, not to freeze the writer on
    input it cannot read (a legacy spelling, a test double).
    """
    p = _ZONE_COLDNESS.get(str(proposed or "").strip().lower())
    c = _ZONE_COLDNESS.get(str(current or "").strip().lower())
    return p is not None and c is not None and p > c


def _current_zone_map(
    db: "DatabaseManager", profile_id: str, facts: list,
) -> dict[str, str]:
    """fact_id -> current zone: the authority overlaid on the mirror.

    The mirror copy comes from the facts this pass already loaded; the
    authority overlay (``fact_retention.lifecycle_zone``) is one indexed read.
    Unrecognized values are dropped -- an unreadable zone must behave like no
    zone, not like a freeze. Best-effort: if the authority read itself fails,
    the mirror map still stands, and if both fail the radius path behaves
    exactly as it did before the guard existed.
    """
    zones: dict[str, str] = {}
    for f in facts:
        lifecycle = getattr(f, "lifecycle", None)
        value = getattr(lifecycle, "value", lifecycle)
        key = str(value or "").strip().lower()
        fact_id = str(getattr(f, "fact_id", "") or "")
        if fact_id and key in _ZONE_COLDNESS:
            zones[fact_id] = key
    try:
        rows = db.execute(
            "SELECT fact_id, lifecycle_zone FROM fact_retention "
            "WHERE profile_id = ?",
            (profile_id,),
        )
        for row in rows:
            d = dict(row)
            key = str(d.get("lifecycle_zone") or "").strip().lower()
            fact_id = str(d.get("fact_id") or "")
            if fact_id and key in _ZONE_COLDNESS:
                zones[fact_id] = key
    except Exception:  # noqa: BLE001 -- the guard must never break maintenance
        logger.debug(
            "lifecycle guard: authority zone read failed", exc_info=True,
        )
    return zones


def _guard_zone_updates(
    updates: "list[tuple[str, str, object]]",
    current_zones: dict[str, str],
) -> "tuple[list[tuple[str, str | None, object]], int]":
    """Clamp radius-proposed zones against the score authority's zone.

    Returns ``(guarded, refused)``. The position always survives. A proposal
    strictly warmer than the authority is written; equal ranks and refused
    coolings come back with the zone set to None, which ``_persist_lifecycle``
    reads as "persist the position, leave the zone alone" -- skipping the
    no-op write keeps ``fact_retention.last_computed_at`` meaning "the score
    authority computed", not "the radius path re-affirmed".
    """
    guarded: list[tuple[str, str | None, object]] = []
    refused = 0
    for fact_id, proposed, position in updates:
        current = current_zones.get(fact_id)
        if current is None or _is_colder(current, proposed):
            guarded.append((fact_id, proposed, position))
            continue
        if _is_colder(proposed, current):
            refused += 1
        guarded.append((fact_id, None, position))
    return guarded, refused
```

`_persist_lifecycle` 扩展(同文件,改 docstring 与 zone 分组循环):

```python
def _persist_lifecycle(
    db: object,
    profile_id: str,
    updates: "list[tuple[str, str | None, object]]",
) -> int:
    """Write a computed tier to the authority AND the mirror, together.

    ...(原 docstring 保留)...

    ``updates`` is ``(fact_id, lifecycle, position_or_None)``. The position has
    no mirror, so it still goes through ``update_fact``; without it the backfill
    would recompute the same facts on every pass and never converge.

    ``lifecycle`` may be None, which the lifecycle guard uses to say "persist
    the position, leave the zone alone": the radius proposed a cooling the
    score authority has not signed, or one it already holds.

    Returns the number of facts whose tier was written.
    """
```

循环改动:

```python
    by_zone: dict[str, list[str]] = {}
    for fact_id, lifecycle, _position in updates:
        if lifecycle is None:
            continue  # position-only: refused cooling or a no-op re-affirm
        by_zone.setdefault(str(lifecycle), []).append(fact_id)
```

- [ ] **Step 5: 跑单元测试确认通过(除 AST 测试)**

```bash
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lcguard/src ~/miniconda3/bin/python -m pytest /tmp/slm-lcguard/tests/test_core/test_lifecycle_guard.py -x -q
```

预期:`TestIsColder`/`TestGuardZoneUpdates` 全过;`TestEveryRadiusWriteSiteIsGuarded` FAIL(写入口尚未接线,`:470` 处 third arg 是 List 不是 `guarded`)。

- [ ] **Step 6: 三写入口接线 + counts + 日志**

`run_maintenance` 内,`facts = db.get_all_facts(profile_id)` 早退判断之后、step 1a 之前:

```python
    # The guard's authority snapshot: built once per pass, before any radius
    # write site runs. Gated the same way as the writers themselves.
    current_zones: dict[str, str] = {}
    if config.math.langevin_persist_positions:
        current_zones = _current_zone_map(db, profile_id, facts)
```

counts 初始化字典加一行(`"langevin_updated": 0,` 之后):

```python
        "langevin_guard_refused": 0,         # radius cooling the score did not sign
```

step 1a 写入口(:468-472 一带)改为:

```python
                weight = ld.compute_lifecycle_weight(position)
                lifecycle = ld.get_lifecycle_state(weight).value
                guarded, refused = _guard_zone_updates(
                    [(f.fact_id, lifecycle, position)], current_zones,
                )
                counts["langevin_guard_refused"] += refused
                _persist_lifecycle(db, profile_id, guarded)
                f.langevin_position = position  # update in-memory for step 1b
```

step 1b batch(:505-511 一带)改为:

```python
            if fact_dicts:
                results = ld.batch_step(fact_dicts)
                guarded, refused = _guard_zone_updates(
                    [(r["fact_id"], r["lifecycle"], r["position"])
                     for r in results],
                    current_zones,
                )
                counts["langevin_guard_refused"] += refused
                _persist_lifecycle(db, profile_id, guarded)
                counts["langevin_updated"] = len(results)
```

Fisher 耦合写入口(:552-555 一带)改为:

```python
                    lifecycle = coupled_ld.get_lifecycle_state(weight).value
                    guarded, refused = _guard_zone_updates(
                        [(f.fact_id, lifecycle, new_pos)], current_zones,
                    )
                    counts["langevin_guard_refused"] += refused
                    _persist_lifecycle(db, profile_id, guarded)
```

完成日志行改为:

```python
    logger.info(
        "Maintenance complete: %d backfilled, %d Langevin, %d Fisher-coupled, "
        "%d guard-refused, %d Sheaf, %d entity-summaries, "
        "%d facts-consolidated, %d code-links",
        counts["langevin_backfilled"], counts["langevin_updated"],
        counts["fisher_coupled"], counts["langevin_guard_refused"],
        counts["sheaf_checked"],
        counts["entity_summaries_consolidated"],
        counts["facts_consolidated"],
        counts["bridge_links"],
    )
```

注意:step 1a 的补种循环上已有的 `if f.langevin_position is not None: continue` 是 ④ 防线的代码本体,在其上方加注释钉死(不改逻辑):

```python
            for f in facts:
                # NO-OVERWRITE, and it is load-bearing: the seed is a
                # measurement taken once. M051 clears positions so this
                # backfill re-measures; if this guard came off, every pass
                # would reseed every fact and the clear/reseed ordering the
                # repair depends on would mean nothing.
                if f.langevin_position is not None:
                    continue
```

- [ ] **Step 7: 跑新测试 + 全部相邻套件(scope gate)**

```bash
cd /tmp/slm-lcguard
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lcguard/src ~/miniconda3/bin/python -m pytest \
  tests/test_core/test_lifecycle_guard.py \
  tests/test_core/test_langevin_init.py \
  tests/test_core/test_a_recomputed_lifecycle_survives_the_tick.py \
  tests/test_core/test_phase5_brain_wiring.py \
  tests/test_core/test_stale_session_close.py \
  tests/test_core/test_key_expander.py \
  tests/test_core/test_decay_uses_the_stores_timescale.py \
  tests/test_compliance/test_lifecycle.py \
  tests/test_mcp/test_mcp_v33_tools.py \
  tests/test_server/test_route_mutation_policy.py \
  tests/test_migrations/ \
  -q
```

预期:全 PASS。特别关注 `test_langevin_init.py` 的 MagicMock 系测试(护栏对不可识别 zone 必须放行)与 `test_a_recomputed_lifecycle_survives_the_tick.py` 的 AST 测试(不得引入 `update_fact({"lifecycle": ...})`)。

- [ ] **Step 8: Commit**

```bash
cd /tmp/slm-lcguard
git add src/superlocalmemory/core/maintenance.py tests/test_core/test_lifecycle_guard.py
git commit -m "fix(lifecycle): score guard on radius-driven zone writes — radius may warm, never cool past the retention score's zone (#136 follow-up)"
```

---

### Task 2: 集成测试——全链路、M051 重跑、生产仿真

**Files:**
- Test: `tests/test_core/test_lifecycle_guard.py`(追加;fixture 与 Task 1 单元测试同文件)
- 不改生产代码。若测试红了,说明 Task 1 实现有误,回 Task 1 修代码而不是松测试。

**Interfaces:**
- Consumes: Task 1 的 `_guard_zone_updates`/`_is_colder`/counts key;既有 `set_fact_lifecycle_zone(db, fact_ids, zone, profile_id=...)`;`M051.apply(conn: sqlite3.Connection)`;`M043.apply(conn)`;`create_all_tables(conn)`;`DatabaseManager(path)`。
- Produces: spec 测试 6/7/8(集成 + e2e);④ 防线的回归钉。

- [ ] **Step 1: 写失败→即过测试(追加到 test_lifecycle_guard.py)**

这些测试钉的是 Task 1 已修好的行为;在 main 上它们全挂(可抽查验证),在本分支应直接通过。

追加 fixture 与三个测试类:

```python
# ---------------------------------------------------------------------------
# Integration fixtures: a real store, not a double
# ---------------------------------------------------------------------------

_SATURATED = [0.35] * 8   # norm 0.9899 — the production signature (§19)
_CENTER = [0.03] * 8      # norm 0.0849 — deep in ACTIVE with step-noise margin


def _cfg() -> SLMConfig:
    cfg = SLMConfig()
    cfg.math.sheaf_at_encoding = False
    cfg.math.fisher_bayesian_update = False
    cfg.math.ebbinghaus_langevin_coupling_enabled = False
    # langevin_persist_positions stays True — the thing under test.
    return cfg


def _insert_fact(
    conn: sqlite3.Connection,
    fact_id: str,
    *,
    lifecycle: str = "active",
    position: list[float] | None = None,
    age_days: int = 60,
    access_count: int = 0,
) -> None:
    from datetime import UTC, datetime, timedelta

    conn.execute(
        "INSERT OR IGNORE INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', ?, 'source')",
        (_PROFILE,),
    )
    created = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
        " lifecycle, langevin_position, access_count, scope, created_at)"
        " VALUES (?, 'm1', ?, ?, ?, ?, ?, 'global', ?)",
        (
            fact_id, _PROFILE, f"content {fact_id}", lifecycle,
            json.dumps(position) if position is not None else None,
            access_count, created,
        ),
    )


def _insert_retention(
    conn: sqlite3.Connection,
    fact_id: str,
    *,
    zone: str,
    score: float,
) -> None:
    conn.execute(
        "INSERT INTO fact_retention (fact_id, profile_id, lifecycle_zone,"
        " retention_score) VALUES (?, ?, ?, ?)",
        (fact_id, _PROFILE, zone, score),
    )


def _zone(db: DatabaseManager, fact_id: str) -> str | None:
    rows = db.execute(
        "SELECT lifecycle_zone FROM fact_retention WHERE fact_id=?",
        (fact_id,),
    )
    return str(dict(rows[0])["lifecycle_zone"]) if rows else None


def _lifecycle(db: DatabaseManager, fact_id: str) -> str:
    rows = db.execute(
        "SELECT lifecycle FROM atomic_facts WHERE fact_id=?", (fact_id,),
    )
    return str(dict(rows[0])["lifecycle"])


def _position(db: DatabaseManager, fact_id: str):
    rows = db.execute(
        "SELECT langevin_position FROM atomic_facts WHERE fact_id=?",
        (fact_id,),
    )
    raw = dict(rows[0])["langevin_position"]
    return json.loads(raw) if raw else None


@pytest.fixture()
def store(tmp_path: Path) -> DatabaseManager:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    create_all_tables(conn)
    conn.commit()
    conn.close()
    return DatabaseManager(str(tmp_path / "memory.db"))


class TestGuardAgainstLiveMaintenance:
    """spec 测试 2/3/5/6: the radius proposes, the score disposes."""

    def test_radius_cannot_archive_a_fact_the_score_keeps(
        self, store, tmp_path,
    ) -> None:
        """The 9/24 production failure, replayed: saturated position, score
        0.9, zone active. One maintenance pass must not move the zone."""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="active", position=_SATURATED)
        _insert_retention(conn, "f1", zone="active", score=0.9)
        conn.commit()
        conn.close()

        counts = run_maintenance(store, _cfg(), _PROFILE)

        assert _zone(store, "f1") == "active"
        assert _lifecycle(store, "f1") == "active"
        assert counts["langevin_guard_refused"] >= 1
        # The radius domain is intact: the position was stepped and
        # persisted, so retrieval weighting still reads it. (spec 测试 5)
        pos = _position(store, "f1")
        assert pos is not None and len(pos) == 8

    def test_score_driven_cooling_arrives_first_then_radius_agrees(
        self, store, tmp_path,
    ) -> None:
        """spec 测试 3/6: legitimate cooling flows through the authority.
        Once the decay cycle's write lands, the radius proposal is the same
        zone and sails through without a refusal."""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="warm", position=_SATURATED)
        _insert_retention(conn, "f1", zone="warm", score=0.42)
        conn.commit()
        conn.close()
        counts = run_maintenance(store, _cfg(), _PROFILE)
        # Radius proposed archived against a warm authority: refused.
        assert _zone(store, "f1") == "warm"
        assert counts["langevin_guard_refused"] >= 1

        # The score recomputes down (what batch_upsert_retention writes).
        set_fact_lifecycle_zone(store, ["f1"], "archive", profile_id=_PROFILE)
        assert _zone(store, "f1") == "archive"

        counts = run_maintenance(store, _cfg(), _PROFILE)
        assert _zone(store, "f1") == "archive"
        assert _lifecycle(store, "f1") == "archived"
        # Equal rank is a skipped no-op, not a refusal.
        assert counts["langevin_guard_refused"] == 0

    def test_warming_is_free(self, store, tmp_path) -> None:
        """A fact the authority cooled may be warmed by the radius — the
        decay cycle will re-cool it next tick if the score disagrees."""
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        _insert_fact(conn, "f1", lifecycle="cold", position=_CENTER,
                     access_count=25)
        _insert_retention(conn, "f1", zone="cold", score=0.3)
        conn.commit()
        conn.close()
        run_maintenance(store, _cfg(), _PROFILE)
        # _CENTER steps stay well inside the ACTIVE band (< 0.20).
        assert _zone(store, "f1") == "active"


class TestM051Rerun:
    """spec 测试 7 + ④ 防线: clear is rerunnable; the reseed happens once."""

    def test_apply_twice_then_reseed_once(self, tmp_path) -> None:
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
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
        # Rerunnable: the second clear has nothing to do and breaks nothing.
        m051.apply(conn)
        conn.commit()
        conn.close()

        db = DatabaseManager(str(path))
        counts1 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts1["langevin_backfilled"] == 2
        assert _position(db, "f1") is not None
        # ④: the seeder never overwrites. Pass two reseeds nothing.
        counts2 = run_maintenance(db, _cfg(), _PROFILE)
        assert counts2["langevin_backfilled"] == 0
        assert _position(db, "f1") is not None


class TestProductionReplay:
    """spec 测试 8: the 9/24 store shape, repaired in the spec's order."""

    def test_saturated_store_repair_sequence(self, tmp_path) -> None:
        from superlocalmemory.storage.migrations import (
            M043_quarantine_display_summaries as m043,
        )
        from superlocalmemory.storage.migrations import (
            M051_lifecycle_is_recomputed_not_resampled as m051,
        )

        path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(path))
        create_all_tables(conn)
        # Two healthy rows the radius convicted (score 1.0, zone active,
        # saturated position) — the 9/23 surgery victims.
        _insert_fact(conn, "g1", lifecycle="active", position=_SATURATED)
        _insert_fact(conn, "g2", lifecycle="active", position=_SATURATED)
        _insert_retention(conn, "g1", zone="active", score=1.0)
        _insert_retention(conn, "g2", zone="active", score=1.0)
        # One row already flipped: archived by the radius, score still 1.0.
        _insert_fact(conn, "p1", lifecycle="archived", position=_SATURATED)
        _insert_retention(conn, "p1", zone="archive", score=1.0)
        conn.commit()

        # ① With the guard in, maintenance holds the line even against a
        # saturated position: nothing cools.
        db = DatabaseManager(str(path))
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert _zone(db, "g1") == "active"
        assert _zone(db, "p1") == "archive"  # equal rank: allowed, no-op
        assert counts["langevin_guard_refused"] >= 2
        db.close()

        # ② M051 re-run: positions cleared so the seed can re-measure.
        conn = sqlite3.connect(str(path))
        m051.apply(conn)
        conn.commit()

        # ③ M043 restore: the score says p1 was wrongly hidden. Its apply()
        # carries its own BEGIN IMMEDIATE/COMMIT, so M051's transaction must
        # already be committed (above) — the same order the production
        # runbook uses.
        m043.apply(conn)
        conn.close()

        db = DatabaseManager(str(path))
        assert _zone(db, "p1") == "active"        # authority restored
        assert _lifecycle(db, "p1") == "active"   # mirror followed

        # ④ Reseed re-measures; old unaccessed facts seed at a decayed
        # radius, and the guard still refuses to let that radius cool what
        # the score keeps. Zones hold; positions exist; nothing reseeds
        # twice.
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 3
        assert _zone(db, "g1") == "active"
        assert _zone(db, "g2") == "active"
        assert _zone(db, "p1") == "active"
        assert _position(db, "p1") is not None
        counts = run_maintenance(db, _cfg(), _PROFILE)
        assert counts["langevin_backfilled"] == 0
        assert _zone(db, "g1") == "active"
        db.close()
```

- [ ] **Step 2: 跑测试确认通过**

```bash
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lcguard/src ~/miniconda3/bin/python -m pytest /tmp/slm-lcguard/tests/test_core/test_lifecycle_guard.py -q
```

预期:全 PASS。若 `TestProductionReplay` 中 M043 恢复断言失败,检查 fixture 的 `memory_id='m1'`(M043 恢复 predicate 要求 `memory_id <> ''`)与 `fact_retention` 行是否插对——不要改 M043。

- [ ] **Step 3: 抽查本测试在 main 上确实全挂(证其非摆设)**

```bash
cd ~/github/superlocalmemory
git status --porcelain   # 预期干净;此步对 main 零写入
env -u ALL_PROXY -u all_proxy PYTHONPATH=$PWD/src ~/miniconda3/bin/python -m pytest /tmp/slm-lcguard/tests/test_core/test_lifecycle_guard.py -q 2>&1 | tail -5
```

预期:大量 FAIL(`_guard_zone_updates` 不存在 / 无护栏半径翻案)。此步用 main 的 src 跑 worktree 的测试文件,纯只读验证。

- [ ] **Step 4: 全量 gate**

```bash
cd /tmp/slm-lcguard
env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lcguard/src ~/miniconda3/bin/python -m pytest tests/ -q -n 8
```

预期:全 PASS。已知 3 个 load flake 若出现,单跑串行复验:`env -u ALL_PROXY -u all_proxy PYTHONPATH=/tmp/slm-lcguard/src ~/miniconda3/bin/python -m pytest <失败测试> -q`,孤立通过即不阻塞。出现任何其他红,停下来修,不跳过。

- [ ] **Step 5: Commit + 合并 main + 推送**

```bash
cd /tmp/slm-lcguard
git add tests/test_core/test_lifecycle_guard.py
git commit -m "test(lifecycle): guard integration — live-maintenance refusal, M051 rerun, production replay"
cd ~/github/superlocalmemory
git merge --no-ff feat/lifecycle-guard -m "merge: lifecycle guard (#136 follow-up; 运维笔记 §19 修复 ①④)"
git push origin main
git worktree remove /tmp/slm-lcguard
```

合并前人工核对 diff 范围(等效 detect_changes):应只有 `maintenance.py` 与新测试文件两个文件被触碰。

---

### Task 3: 生产窗口——② M051 重跑 + ③ M043 恢复 + 部署验证

**此任务为生产操作,每一步执行前需主人确认(🔒)。全程由主人在场执行,不是 subagent 任务。**

**Files:**
- 无代码改动。操作对象:`$HOME/.superlocalmemory/memory.db`(生产)。

**Interfaces:**
- Consumes: Task 1/2 已合并 main(①④ 代码已部署);`M051.apply`/`M043.apply` 可重入语义(Task 2 已钉)。
- Produces: 生产库 position 清零 + 误归档行恢复;Task 4 判别实验的基线读数。

**前置基线(只读,先记录):**

```bash
sqlite3 -readonly $HOME/.superlocalmemory/memory.db \
  "SELECT lifecycle, COUNT(*) FROM atomic_facts GROUP BY 1;"
sqlite3 -readonly $HOME/.superlocalmemory/memory.db \
  "SELECT COUNT(*) FROM atomic_facts WHERE langevin_position IS NOT NULL;"
sqlite3 -readonly $HOME/.superlocalmemory/memory.db \
  "SELECT lifecycle_zone, COUNT(*) FROM fact_retention GROUP BY 1;"
```

记录三组数字到运维笔记(判别实验的对照组)。§19 读数:archive 3,306-3,309;position 饱和 99% 在 0.99。

- [ ] **Step 1: 🔒 停 gateway,再停 daemon,验证端口释放**

按 §17 已验证的有序重启流程:先停 hermes-gateway(zhihui 侧),再:

```bash
slm serve stop
sleep 2
lsof -i :8765 || echo "port free"
cat $HOME/.superlocalmemory/daemon.pid 2>/dev/null || echo "pid file gone"
```

若 pid 残留(§17 有孤儿 PID 先例):`kill <pid>`,再验 `lsof -i :8765` 为空。**端口未释放不得继续**——迁移需要独占写锁。

- [ ] **Step 2: 🔒 备份**

```bash
sqlite3 $HOME/.superlocalmemory/memory.db \
  ".backup '$HOME/.superlocalmemory/backups/memory-pre-lifecycle-guard-2026-09-24.db'"
ls -la $HOME/.superlocalmemory/backups/memory-pre-lifecycle-guard-2026-09-24.db
```

确认备份文件存在且大小与 memory.db 同量级。

- [ ] **Step 3: 🔒 ② M051 重跑 + ③ M043 恢复(同一会话,顺序不可反)**

```bash
env -u ALL_PROXY -u all_proxy PYTHONPATH=$HOME/github/superlocalmemory/src \
  ~/miniconda3/bin/python - <<'PYEOF'
import os
import sqlite3
from superlocalmemory.storage.migrations import (
    M051_lifecycle_is_recomputed_not_resampled as m051,
    M043_quarantine_display_summaries as m043,
)

db_path = os.path.expanduser("~/.superlocalmemory/memory.db")
conn = sqlite3.connect(db_path)

before_pos = conn.execute(
    "SELECT COUNT(*) FROM atomic_facts WHERE langevin_position IS NOT NULL"
).fetchone()[0]
m051.apply(conn)
conn.commit()
after_pos = conn.execute(
    "SELECT COUNT(*) FROM atomic_facts WHERE langevin_position IS NOT NULL"
).fetchone()[0]
print(f"M051 rerun: positioned {before_pos} -> {after_pos}")

before_zones = dict(conn.execute(
    "SELECT lifecycle_zone, COUNT(*) FROM fact_retention GROUP BY 1"
).fetchall())
m043.apply(conn)  # 自带 BEGIN IMMEDIATE/COMMIT
after_zones = dict(conn.execute(
    "SELECT lifecycle_zone, COUNT(*) FROM fact_retention GROUP BY 1"
).fetchall())
print(f"M043 restore: zones {before_zones} -> {after_zones}")
conn.close()
PYEOF
```

预期:`positioned 3373+ -> 0`;zones 中 archive 大幅下降(目标 <5%,spec §5)。migration_log 不受影响(直接调 apply,不改 DDL 文本,无哈希漂移;M051/M043 行保持 complete)。

- [ ] **Step 4: 🔒 起 daemon,验证首轮维护,再起 gateway**

```bash
slm serve start   # 或既有启动方式;daemon 启动即跑首轮维护
sleep 90
grep -E "Langevin backfill|guard-refused|Maintenance complete" \
  $HOME/.superlocalmemory/logs/*.log | tail -10
sqlite3 -readonly $HOME/.superlocalmemory/memory.db \
  "SELECT lifecycle, COUNT(*) FROM atomic_facts GROUP BY 1;"
sqlite3 -readonly $HOME/.superlocalmemory/memory.db \
  "SELECT COUNT(*) FROM atomic_facts WHERE langevin_position IS NOT NULL;"
```

预期:日志见 `Langevin backfill: N facts initialized`(N≈3,300+,补种发生);`guard-refused` 计数 >0(护栏正在吸收半径压力);zone 分布与 Step 3 后基本持平(护栏下维护不再翻案);position 重新填充。健康检查 ready 后,按 §17 流程起 hermes-gateway,验证 health ready / recall_healthy。

- [ ] **Step 5: 🔒 三发探针 + 召回面验证**

用 §17/§18 的黄金样本探针(`米家日常` 应回 a4267ec0 / 00f7db20 / b7e9fe5d 三条;`迷雾小镇` 应有真分数结果——§19 时它因 44/53 条相关记忆被归档而归零,恢复后应回升)。结果记录运维笔记。

**回滚预案:** 任一步异常 → 停 daemon → `cp` 备份文件回 memory.db(先删 -wal/-shm)→ 起 daemon → 起 gateway → 运维笔记记录中止点。

---

### Task 4: 判别实验守望 + 记录回写

**Files:**
- Modify: `docs/运维笔记-2026-09-22-桥侧与维护层挂账.md`(追加 §20)
- Modify: `docs/上游贡献清单.md`(P1 条目状态行)

**Interfaces:**
- Consumes: Task 3 的基线读数与探针结果;知惠 08:30 cron 守望读数。
- Produces: §20 实施记录;P1 条目从"材料已齐"更新为"已实现待提 PR"。

- [ ] **Step 1: 02:00 维护窗判别实验(次晨验证)**

次晨(或知惠 cron 复查后)读数:

```bash
sqlite3 -readonly $HOME/.superlocalmemory/memory.db \
  "SELECT lifecycle, COUNT(*) FROM atomic_facts GROUP BY 1;"
grep -E "guard-refused|Maintenance complete" \
  $HOME/.superlocalmemory/logs/*.log | tail -20
```

**判别标准(spec §5):** 02:00 窗口过后 archive 不批量回升(对比 Task 3 基线,波动 <100 行量级)。guard-refused 在日志中持续出现是护栏吸收半径压力的证据,不是故障。若 archive 批量回升 → 护栏被绕过,立即回 Task 1 查写入口,不得先动数据。

- [ ] **Step 2: 运维笔记 §20 回写**

在 `docs/运维笔记-2026-09-22-桥侧与维护层挂账.md` 末尾追加 §20,内容要点:① 修复四步的执行记录(commit 哈希、生产窗口时间、M051/M043 打印的实际数字);② 判别实验结论(基线 vs 次晨);③ 召回探针终态。沿用 §17-§19 的既有格式。

- [ ] **Step 3: 上游贡献清单 P1 状态更新 + commit**

`docs/上游贡献清单.md` P1(ELC 时钟/lifecycle guard 叙事)条目追加一行:`2026-09-24 已在 fork 实现并生产验证(commit <hash>);PR 标题 "lifecycle guard: don't let Langevin positions supersede the store-scaled retention score",材料齐,待主人拍板提交。`

```bash
cd ~/github/superlocalmemory
git add docs/运维笔记-2026-09-22-桥侧与维护层挂账.md docs/上游贡献清单.md
git commit -m "docs: 运维笔记 §20 lifecycle guard 实施记录 + 上游清单 P1 状态"
git push origin main
```

---

## Self-Review 记录(计划落盘前已执行)

- **spec 覆盖:** §4 ①→Task 1(三写入口,见"Spec 符合性说明"1);②→Task 3 Step 3;③→Task 3 Step 3;④→Task 1 Step 6 注释钉 + Task 2 `TestM051Rerun`/`TestProductionReplay` 回归钉(见说明 2)。§5 测试 1/2/3→`TestIsColder`+`TestGuardZoneUpdates`+`TestGuardAgainstLiveMaintenance`;4→AST 测试;5→`test_radius_cannot_archive...` 的 position 断言;6→`test_score_driven_cooling...`;7→`TestM051Rerun`;8→`TestProductionReplay`;生产验证→Task 3/4。§6 上游 PR→Task 4 Step 3(提交本身等拍板,spec §7)。
- **占位符扫描:** 无 TBD/TODO;所有代码块为完整可复制实现。
- **类型一致性:** `_guard_zone_updates` 入参 `(str, str, object)` 出参 `(str, str | None, object)` 与 `_persist_lifecycle` 新签名一致;三写入口变量名统一 `guarded`(AST 测试依赖);counts key `langevin_guard_refused` 全链路一致。
- **AST 测试自校验:** 修正过一轮——初版对"未接线的 List 参数"判过(接线前也会绿);现版通过识别 ELC 站的唯一签名(单行 List + Tuple 第三位 Constant None)区分,接线前必红、接线后才绿。
