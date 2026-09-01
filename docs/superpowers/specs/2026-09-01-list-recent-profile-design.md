# list_recent per-request profile 与结果完整度规格设计

- 日期:2026-09-01
- 状态:设计已批准(三节逐节确认),待 spec 审阅
- 需求书:`docs/deepmaid-recent-需求书-2026-09-01.md`(R1–R3;deepmaid 侧消费验收见其 §5)
- 关联:PR #127(per-request profile 路由主链)的收尾补丁;同主体叙事
- 上游基线:已含 PR #127 基线(4.1.11 merge,85483816);无新 merge 前置

## 1. 背景与目标

deepmaid M1c 晨报链路需要“最近 N 条记忆”做摘要素材(纯时间序,不做事先检索)。fork 已有 MCP 工具 `list_recent(limit)`,但离 deepmaid 的消费条件差三口气:无 `profile_id` 参数(跟全局指针)、content 截断 120 字符、无 `importance`。

**目标**:`list_recent` 与 daemon `/list` 补 `profile_id` 穿透(语义与 PR #127 完全一致),结果完整(不截断 + 补 importance),daemon `/list` 顺带从断点修复(`engine.list_facts` 在 4.1.x 已不存在,当前生产 500)。

**非目标**:不动 `recall` 的语义检索与 abstain;不引入 per-profile ACL;不碰模型工具面(`memory_save`/`memory_search` 不变,host 侧管理面读工具定位)。

## 2. 已批准的关键决策

| 决策点 | 结论 |
|---|---|
| daemon 层 | **补齐**:`/list` 加 `profile_id` + 修复 `engine.list_facts` 缺失断点,与 MCP 语义一致 |
| content 截断 | 上游不再截断(移除 120/100 字符斩),截断预算归调用方(deepmaid 的 `recentMaxChars`) |
| importance | 补进结果(fetch 已有同名字段) |
| 空命名空间 | `success:true, results:[], count:0`,不走 abstain(纯时间序读无语义判断) |
| allowlist | `list_recent` 已在 `SLM_MCP_TOOLS` 域内,启用时 deepmaid 侧追加即可 |

## 3. 架构与数据流

```
MCP 工具 list_recent(profile_id="", limit=10)
  └─ _runtime_profile(get_engine, explicit=profile_id)
     │   explicit 非空 → 直接用(跳过 daemon /status 往返,顺带省一次 RTT)
     │   空 → 现状(经 daemon /status 解析活跃 profile)
     ├─ daemon 在跑 → GET /list?profile_id=&limit=
     │                  ├─ 非空:profiles 表校验 → ghost 404 unknown_profile
     │                  │          通过 → engine.list_facts(profile_id=pid, limit=...)
     │                  └─ 空:engine.list_facts()(活跃 profile)
     └─ daemon 不在 → 本进程 engine.list_facts(profile_id=profile_id or None)(语义一致)
```

规则:

1. **空 = 逐字节现状**(活跃 profile),兼容锚点。
2. **非空 = 纯路由**,不读不改全局活跃指针与 `profile_generation`。
3. **校验前置**:daemon `/list` 先查 `profiles` 表,不存在立即 404 + `unknown_profile`,不触引擎;与 `/remember` `/recall` 同构。
4. **content 不截断**、**importance 在场**、`fact_type`/`session_id` 既有字段保留。
5. **空 profile/无事实**:`{success:true, results:[], count:0}` 直接返回,无 abstain。

## 4. 引擎/DB 层落点

- **DB 层**:`get_all_facts(profile_id, limit=..., *, include_global=..., include_shared=...)` 已完整参数化(LIMIT 下推、newest-first),**零改动**。
- **Engine 层**:补一个入口,与 recall/store 同一 `pid = profile_id or self._profile_id` 惯例:

```python
def list_facts(
    self, limit: int = CANONICAL_LIST_LIMIT, profile_id: str | None = None,
) -> list[AtomicFact]:
    pid = profile_id or self._profile_id
    return self._db.get_all_facts(pid, limit=limit)
```

  放在 `recall` 附近,供 daemon `/list` 与 MCP 离线回落共用——穿透逻辑单点收敛。
- **daemon 层**:`/list` 不再调 `engine.list_facts()` 旧名,改调新入口;移除 `f.content[:100]` 截断;补 `importance`。
- **MCP 层**:`list_recent` 加 `profile_id: str = ""`;`_runtime_profile(..., explicit=profile_id)` 既有 `if explicit: return explicit` 语义直接用;离线回落传给 `engine.list_facts`。

## 5. 错误处理与兼容

| 场景 | 行为 |
|---|---|
| 非空且不存在 | 404 + `{"success":false,"error":{"code":"unknown_profile"}}`,不触引擎 |
| 空 | 现状(活跃 profile),兼容锚点 |
| 等于活跃 | 正常路由 |
| daemon 离线 + MCP 带参 | 回落本进程引擎同参 |

`_runtime_profile` 的 explicit 分支有既有语义(非空即返回,跳过 daemon 往返);离线回落路径用 `engine.list_facts(profile_id=...)` 新签名。

## 6. 测试与验收

单测(`tests/test_server/test_list_recent_profile.py` + MCP 扩充):

1. 穿透与隔离:doris/zhihui 双 profile,各自只命中己方
2. 全局零副作用:前后 `/status` 的 profile 与 profile_generation 不变
3. 结果完整:content 无截断、importance 在场、created_at 降序
4. 空命名空间:空 profile → success:true + results:[],无 abstain
5. 兼容:不带参数落活跃 profile
6. daemon 通路 + 离线回落:两条路径语义一致
7. `engine.list_facts` 复活:daemon `/list` 不再 500,真实返回(顺带修复生产断点)

MCP stdio 通路:`SLM_MCP_TOOLS` 含 `list_recent` 时全通,不含时不可见。

回归门:全量套件 + hermes 118 + 晨报链路现有测试。

前置验证(30 秒):curl 实证当前 daemon `/list` 是否 500,坐实“从坏到好”叙事。

## 7. 上游 PR 打包

- 叙事:PR #127 的收尾补丁——同一主体(profile 穿透)补完 host 侧管理面读工具;“从断点修复到语义完整”的干净叙事。
- 策略:若 #127 未合并,等它落地再提(避免依赖悬空);若已合并,独立小 PR 直上。
- 预期 review 焦点:`content` 不截断的行为变更理由(截断预算归调用方,上游先斩 120 是 preempt);`engine.list_facts` 缺失是 4.1.x 引擎重构的回归(daemon 端点 500),附带证据。

## 8. 后续(不在本 spec 范围)

- deepmaid 侧消费(需求书 §5):provider `supportsRecent` → true,`recent(ns, limit)` 映射 `list_recent(profile_id=ns, limit)`,晨报车道验收
- PR #127 的两个已知残余(空查询早退/keyword-fallback 的 profile 回显;路由写入的 enrich 时延)独立跟踪
