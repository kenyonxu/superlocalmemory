# MSLM 需求书：list_recent 的 per-request profile 与结果完整度

> 日期：2026-09-01 · 提出方：deepmaid-agent（M1c 晨报链路）
> 本文件同步存放于 deepmaid-agent 与 superlocalmemory 两仓；**实现以本仓（superlocalmemory）为准**，deepmaid 侧消费验收见 §5。
> 定位：host 侧管理面读工具（模型工具面不变），优先级低——deepmaid 侧已按 `supportsRecent: false` 降级（空摘要 + warn 一次），落地前无任何阻塞。
> 现状事实按 superlocalmemory fork commit `5cd181dd`（2026-09-01）源码核实；实现以 superlocalmemory 仓为准，deepmaid 侧消费验收见 §5。

## 1. 背景与问题

deepmaid M1c 的晨报链路（`@deepmaid/maid-cron`）每天 08:00 生成晨报时，需要「该女仆最近 N 条记忆」作摘要素材：按时间倒序的近因列表，不做事先检索。为此 maid-memory seam 已扩出 `recent(ns, limit)` 读路径与能力声明（`ProviderCapabilities.supportsRecent`，照 `forget` 先例）；local 假件已实现，MSLM provider 暂声明 `false` 降级。

**问题：deepmaid 消费的 MCP 四件套（`remember,recall,get_status,switch_profile`）没有可用的「最近 N 条」读法。**

- `recall` 是语义检索：喂「最近」语义会丢时序近因，且每次晨报多一遍 embedding 检索成本；
- fork 的 MCP 面其实已有 `list_recent(limit)`（readOnlyHint，`get_all_facts(pid, limit)` 按 created_at DESC），但离 deepmaid 的消费条件差三口气（见 §2）。

## 2. 现状事实（2026-09-01 源码核实，superlocalmemory fork）

- `list_recent(limit: int = CANONICAL_LIST_LIMIT)`（`mcp/tools_core.py:537`）：**无 `profile_id` 参数**——经 `_runtime_profile` 解析，未显式指定时落到引擎/daemon 全局活跃 profile；
- `remember`/`recall` 已接受可选 `profile_id`（per-request profile 需求书已落地）：显式锚定命名空间、不读不改 daemon 全局档——`list_recent` 与该语义不对齐，是多女仆（M2 知惠上线）下的硬阻塞；
- 结果形状 `{ success, results: [{ fact_id, content, fact_type, created_at, session_id }], count }`：`content` **截断到 120 字符**（`f.content[:120]`），且**无 `importance`** 字段（`fetch` 有、`list_recent` 漏）；
- 工具名已在 `SLM_MCP_TOOLS` allowlist 域内（`mcp/profiles.py`），可选启停通路现成。

## 3. 需求

### R1（核心）：profile_id 穿透 + 结果完整

```
list_recent(profile_id: str = "", limit: int = 10)
  -> { success: bool,
       results: [{ fact_id, content, created_at, importance }, …],
       count: int }
```

- 按 `created_at` 倒序（newest first，维持现状）；
- `profile_id` 语义与 `remember`/`recall` 完全一致：携带时该次读**只**作用于该 profile，不读取也不变更 daemon 全局活跃 profile；不携带时维持现状（落当前活跃 profile，向后兼容）；
- `content` 不在上游截断（或提供 `max_content_chars` 参数且默认值 ≥ 500）：截断预算归调用方管——deepmaid 侧晨报渲染有自己的 `recentMaxChars` 预算，上游先斩 120 字符会 preempt 调用方预算；
- `importance` 进结果（`fetch` 已有同名字段，补齐即可）；`fact_type`/`session_id` 等既有附加字段保留不动（additive，无碍）。

### R2：allowlist 定位为 host 侧管理面工具

- 进 `SLM_MCP_TOOLS` allowlist 可选启停（deepmaid 侧启用时把 `list_recent` 追加进 allowlist 串）；
- deepmaid 侧定位：**host 侧管理面读工具**（`@deepmaid/maid-memory-mslm` provider 内部调用），模型的记忆工具面（`memory_save`/`memory_search`）不变，不向模型暴露。

### R3：空命名空间语义

- 空 profile（或该 profile 无 fact）返回 `{ success: true, results: [], count: 0 }`；
- **不走 abstain**：`recall` 的 evidence_floor abstain 是语义检索的判断，recent 是纯时间序读，无语义判断参与。

## 4. 验收场景（superlocalmemory 侧自测）

1. **穿透与隔离**：daemon 常驻双 profile；带 `profile_id=doris` 的 `list_recent` 只返回 doris 的 facts（newest first），带 `profile_id=zhihui` 互不可见；
2. **全局零副作用**：上述调用前后 `get_status()` 的 `profile` 与 `profile_generation` 不变；
3. **结果完整**：content 无 120 截断、importance 在场、created_at 降序；
4. **空命名空间**：新建空 profile → `success: true, results: [], count: 0`，无 abstain 文案；
5. **兼容**：不带 `profile_id` 的旧调用行为不变（落当前活跃 profile）；
6. **MCP 通路**：`mslm mcp` 子进程经 `SLM_MCP_TOOLS` allowlist 启用后调用全通，未启用时工具不可见。

## 5. deepmaid 侧消费与回归（实现完成后）

1. `@deepmaid/maid-memory-mslm` provider：`capabilities.supportsRecent` → `true`，`recent(ns, limit)` 映射 `list_recent(profile_id=ns, limit)`（`fact_id→id`、`created_at→createdAt` 字段映射与 `title` 的取法实现期定，对齐 `RecentMemory` 形状）；
2. `sync-home` 的 mslm 行 `SLM_MCP_TOOLS` 增补 `list_recent`；
3. 晨报车道：mslm provider 下晨报记忆摘要非空、「不支持 recent」warn 消失；
4. 回归：`npm run check` 全绿；M2 多女仆互不串摘要（同 daemon 双 profile 交错）。

## 6. 关联文档

- 前置需求：[mslm-per-request-profile-需求书-2026-08-30.md](mslm-per-request-profile-需求书-2026-08-30.md)（R1 的 profile_id 语义即承它）
- deepmaid 规格：[m1c-cron-delivery-design-2026-09-01.md](m1c-cron-delivery-design-2026-09-01.md)（晨报链路与 recent 读路径）
