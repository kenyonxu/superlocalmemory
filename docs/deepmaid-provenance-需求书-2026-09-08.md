# MSLM 需求书:fact 来源/性质标注(provenance)与策展读写面

> 日期:2026-09-08 · 提出方:deepmaid-agent(M3b 共享知识层治理前置)
> 定位:host 侧管理面与数据模型需求(模型的记忆工具面不变),优先级中——不阻塞 M3a;M3b 三闸门的数据模型前提。落地前 deepmaid 侧按 §5-4 兜底路径降级,无硬阻塞。
> 现状事实按 superlocalmemory fork commit `84f3187c`(2026-09-08)源码核实;实现以 superlocalmemory 仓为准,deepmaid 侧消费验收见 §5。
> 需求依据:[女仆的一天·九幕体验定稿](m3-ux-女仆的一天-九幕体验定稿-2026-09-08.md)「共享知识层(global scope)治理方案」节——共享层放「世界事实」不放「记忆」;**deepmaid 编排,SLM 存取**:SLM 只做数据模型与读写面,不懂女仆团规则。

## 1. 背景与问题

deepmaid M3b 要落地女仆团共享知识层(global scope)的三闸门治理:**写入门槛**(女仆日常写入 global 默认降级 personal,授权/策展才放行)、**读出边界**(共享层注入打「背景事实」低权重标签)、**策展修订**(118 条 hermes 时代 global 遗产分拣:世界常识升 curated、知惠私人降 personal、垃圾清除;doris personal 两条脏数据同批)。

三闸门是 deepmaid 侧编排,但有一个数据模型前提只能由 SLM 落:**fact 级的来源/性质标注**——一条 global 记忆是「世界事实(主人是谁/馆的布局)」还是「历史遗产未分拣」,读面必须能区分,否则读出边界和策展扫描都无从谈起。受控词表(九幕定案):`world-fact` / `maid-private` / `curated` / `legacy`,可扩展,缺省 null(未标注)。

**问题:fork 现状没有任何「检索单元级」的标注字段与修订通道(见 §2)。**

## 2. 现状事实(2026-09-08 源码核实,fork `84f3187c`)

- `remember` 已接受 `tags: str` 与 `scope`(personal/shared/global);但 **tags 只落原始记忆层 `metadata["tags"]`**(engine.py:513-514、unified_daemon.py:4837-4838),不进检索单元;
- **`AtomicFact`(检索单元,storage/models.py:170-225)无 tags、无 provenance、无 per-fact metadata 字段**;有 scope/shared_with/fact_type/importance/lifecycle;
- 读面回显:`search`(fact_id/content/fact_type/confidence/date)、`fetch`(+entities/importance/lifecycle/access_count)、`list_recent`(fact_id/content/fact_type/created_at/importance/session_id,unified_daemon.py:5483-5490)**均无 scope/tags 回显**;recall 引擎侧 RetrievalResult 包装 AtomicFact(scope 在对象图内),MCP 线面序列化未见 scope/tags 键;
- 修订面:`update_memory(fact_id, content)` **只能改 content**,且「fact_id must belong to the active profile」——无 profile_id 参数,改不了标注,也改不了 scope;
- 删除面:`delete_memory(fact_id)` 已在 MCP 注册域(profiles.py:72),同样 active profile 制;
- 检索面无任何 tag/provenance 过滤参数;管理面有 `engine.list_facts(limit, profile_id)`(/list 路由)可作策展扫描底座。

## 3. 需求

### R1(核心·数据模型):AtomicFact 增 `provenance` 受控标注

- 字段落**检索单元**(AtomicFact),不停留原始记忆层;受控词表 `world-fact` / `maid-private` / `curated` / `legacy`,可扩展,缺省 null(未标注);
- 与既有 `tags` 分层不合并:tags 是自由文本检索提示,provenance 是治理门禁语义——门禁判定不解析自由串;
- 词表外取值拒绝(或归 null)——受控性是门禁判定的前提。

### R2(写入):remember 增可选 `provenance` 参数

- 缺省 null;不带该参数的旧调用字节不变(向后兼容);`profile_id` 穿透语义不动。

### R3(修订·策展核心动作):标注与 scope 的原位修订

- `update_memory` 扩展可选 `provenance` / `scope` 参数,或新增 `annotate_memory(fact_id, provenance?, scope?)`——二选一由下游定,语义要求:
  - provenance 可设可清(归 null);
  - **scope 可迁移**(personal ↔ global)——「升 curated 入共享层 / 降回 personal」是 118 条分拣的核心动作;
  - content 不变时不必传 content;
- **可选 `profile_id` 穿透**(语义同 remember/recall/list_recent:带则只作用该 profile、不读不改 daemon 全局活跃指针);delete_memory 同款增补——多女仆共处一个 daemon 时,策展/清理不能靠 switch_profile 搬全局指针。

### R4(读面回显):结果项增 `scope` + `provenance`

- recall/search/fetch/list_recent 结果项统一增 `scope`、`provenance` 两键(additive)——读出边界闸门的输入:deepmaid 侧按 provenance 决定注入权重(「背景事实」低权重标签)。

### R5(策展扫描):管理面列表读支持过滤

- `engine.list_facts`(/list 路由)或 list_recent 增 `scope` / `provenance` 过滤参数,**含 `provenance` 为 null(未标注)的显式筛选**——「global 里还没分拣的遗产有哪些」是 118 条盘点的第一问;
- 定位 host 侧管理面读工具,不进模型工具面。

### R6(定位与兼容)

- 新参数/新工具进 `SLM_MCP_TOOLS` allowlist 可选启停;
- 全部 additive:不带新参数的旧调用行为不变;
- 非目标(SLM 不背平台规则):写入门禁判定、策展规则、注入降权策略全在 deepmaid 侧;SLM 只提供存取与标注。

## 4. 验收场景(superlocalmemory 侧自测)

1. **写入回环**:remember 带 `provenance=world-fact` → recall/fetch/list_recent 结果项回显 `provenance: "world-fact"` 与 `scope`;
2. **原位修订**:一条 personal 事实经修订迁 global 且标 curated → 再读回显新 scope/provenance;content 缺省时修订合法、原文不变;
3. **穿透与隔离**:daemon 常驻双 profile;带 `profile_id` 的修订/删除只作用该 profile,前后 `get_status()` 的 `profile` 与 `profile_generation` 不变;
4. **策展扫描**:构造 global 混合集(3 条已标注 + 2 条未标注)→ 按 `scope=global` + `provenance=null` 过滤恰返回 2 条;
5. **词表约束**:provenance 词表外取值被拒或归 null;
6. **兼容**:不带新参数的旧调用行为字节不变;`SLM_MCP_TOOLS` 未启用时新工具不可见。

## 5. deepmaid 侧消费(实现完成后)

1. **写入门槛**(maid-memory seam):`memory_save(scope=global)` 守卫——女仆日常调用降级 personal;durable 事件带「主人授权共享」标记 → `remember(scope=global, provenance=world-fact)`;策展批处理 → `provenance=curated`;
2. **读出边界**:召回结果项按 provenance 打「背景事实」低权重标签进注入(回答「主人是谁」,不参与「上次聊到哪」),配置语法与 Mode-Bridge retrieval_profiles 融合;
3. **策展技能**(知惠任策展人):R5 扫描 → R3 分拣(升降 scope + 打标;垃圾 delete_memory)→ doris personal 两条脏数据同批清理;
4. **兜底降级(R3 未落地前)**:scope 迁移走 `delete_memory` + `remember` 重写(接受 fact_id 变迁——历史条目无外部引用);标注类标签待 R1/R2 落地后补打。

## 6. 关联文档

- 需求依据:deepmaid 仓《女仆的一天·九幕体验定稿》收官「共享知识层治理方案」节;总规格修订 5;roadmap M3b 节
- 前置需求:[mslm-per-request-profile-需求书-2026-08-30.md](mslm-per-request-profile-需求书-2026-08-30.md)(R3 的 profile_id 语义承它)、[mslm-recent-需求书-2026-09-01.md](mslm-recent-需求书-2026-09-01.md)(list_recent 管理面先例)
- 副本:superlocalmemory 仓 `docs/deepmaid-provenance-需求书-2026-09-08.md`

## 7. 实施记录(2026-09-13 落地)

**实现**:`main @ 93ff8deb`(fork superlocalmemory),2026-09-13 合入并完成生产升级(daemon M052 迁移自动执行,存量 12,481 条全部 NULL,无 backfill)。

**规格与计划**:spec `docs/superpowers/specs/2026-09-13-provenance-kind-design.md`;实施计划 `docs/superpowers/plans/2026-09-13-provenance-kind.md`(5 任务全部评审通过,全量门 11395 passed 零新增失败)。

**关键落地决策**(与需求书的差异点):

| 需求书原文 | 实际落地 | 理由 |
|---|---|---|
| 字段名 `provenance` | **`provenance_kind`** | DB 已有 `provenance` 血缘表(来源追踪),同名混淆;治理标注与来源追踪分层 |
| 词表 `world-fact` / `maid-private` / `curated` / `legacy` | **`world` / `private` / `curated` / `legacy`** | 通用化对外发布(上游 PR 不带平台词汇);deepmaid 侧映射 `world-fact→world`、`maid-private→private` |
| R3 "新增 annotate_memory 或扩展 update_memory 二选一" | **扩展 `update_memory`** | 一个工具面收敛;content 修订沿用既有 correction 链(fact_id 变迁),仅 scope/标注的修订走 in-place(**fact_id 不变迁**——策展不破坏历史引用) |
| R1 "词表外拒绝或归 null 二选一" | **写入归 null / 扫描与修订 400** | 策展场景"未知=未分拣"最安全;扫描是受控操作面不容模糊 |

**deepmaid 消费注意事项**(§5 适配前必读):

1. **MCP 面不能清标注、不能迁 `scope=shared`**——`""` 参数语义是"不变";清标注(`provenance_kind: null`)与 shared 迁移(带 `shared_with`)走 daemon HTTP 面(`PATCH /api/memories/{id}`)。§5-3 策展流(升 curated/降 personal/删除)不受影响。
2. **一次 `remember` 的派生 fact 不继承标注**——标注只落在 queryable fact(`fact_ids` 回执那条);注入层不得假设一次写入的 fact 集均匀标注。
3. 扫描是 ownership 域(`get_all_facts` 默认不含他 profile 的 global)——恰好匹配"盘点自己 profile 里的 global 遗产"。
4. `provenance_kind=null` 字面量筛选未标注(大小写不敏感);`scope`+`provenance_kind` 可组合。

**验收场景对账**(§4 → 实测):

1. 写入回环 ✅(三读工具回显,spec 验收 1);2. 原位修订 ✅(fact_id 稳定+successor 继承标注,验收 2);3. 穿透与隔离 ✅(指针/generation 冻结,e2e);4. 策展扫描 ✅(null 显式筛选恰返未标注,验收 4);5. 词表约束 ✅(写入归 null/扫描 400 双语义);6. 兼容 ✅(journal 字节 sha256 恒等,wire 五组钉死)。

**已知边界**(fork ledger 存档):MCP `delete_memory` 对 correction-history 保护事实的 409 会塌缩成 retryable 信封(既有缺陷,非本特性引入);`recall_trace` 调试工具不回显两新键(§5 四读面之外)。

**上游 PR**:材料就绪(squash 方案与 PR 描述要点在实施 ledger),建议随下一个上游窗口提交——词表通用化叙事("SLM 只提供受控词表与读写面,不背平台规则")。
