# per-request profile 规格设计

- 日期:2026-08-30
- 状态:已实施(2026-08-31),见 CHANGELOG
- 需求书:`docs/deepmaid-per-request-profile-需求书-2026-08-30.md`(R1–R6;deepmaid 侧消费验收见其 §6)
- 关联:方案 B(EmbeddingService daemon fallback)同模式的 fork 先行 → 上游 PR 路线
- 上游基线:本 fork 落后 upstream/main 120 提交;实施前必须先 merge upstream(实施计划 Task 0)

## 1. 背景与目标

daemon 的活跃 profile 是全机单点有态:`switch_profile` 改全局指针,同一数据根的所有客户端共享。多客户端(知惠/hermes + Doris/deepmaid)共存时,任一客户端切换后,另一客户端不带显式 profile 的读写被静默改道(顺序交替即触发)。deepmaid 现用轮询防御(per-op `get_status` 校验),治标且有 hermes 侧盲区。

**目标**:`remember`/`recall` 接受可选 `profile_id` 请求参数,携带时该次操作只作用于该 profile、不读不改全局活跃指针;不携带时行为逐字节等于现状。单 daemon、单 embedding worker、单 LLM 服务全部 profile。

**上游核查结论(2026-08-30)**:上游无 per-request profile;4.1.x 给 `/remember` 加了 `RememberRequest.profile_id` 作为 **compare-and-write 409 守卫**(绑错 profile 即拒绝,注释明言"daemon remains single-profile");`engine.recall(query, profile_id=None)` 已参数化(`pid = profile_id or self._profile_id`),retrieval 全通道按调用级 pid 查询。缺口:`engine.store` 无 profile_id 参数、daemon 不路由、MCP 面不暴露、entity 邻接缓存单槽(交错即整包重载)。

## 2. 已批准的关键决策

| 决策点 | 结论 |
|---|---|
| 内部机制 | **A:请求级穿透**(单引擎,store 补参数,daemon/MCP 逐层穿透);per-profile 引擎池否决(与上游单引擎架构分歧大、每引擎一套检索栈内存、需注入共享 embedder 避免进程内 flock 自冲突即方案 B 自委托环) |
| 上游 409 守卫 | **路由取代守卫**:带 profile_id 的请求直接路由,永不 409;守卫代码保留但对新语义不可达(上游 PR 叙事点:stale client 从"失败"变"写对") |
| R4 可选项(get_status 带 profile_id 查统计) | **不做**(YAGNI,deepmaid 消费面不依赖) |
| 兼容锚点 | `profile_id` 为空 = 逐字节现状,存量客户端(hermes/CLI/dashboard)零改动零感知 |

## 3. 架构与数据流

```
MCP 工具面 (tools_core.py)                daemon 路由 (unified_daemon.py)
remember(profile_id="") ──write-thru──▶ POST /remember {profile_id}
recall(profile_id="")   ──write-thru──▶ GET /recall?profile_id=...
                                          非空: ①profiles 表校验存在性
                                                  不存在 → success:false
                                                  + error_code:"unknown_profile"
                                                ②engine.store/recall(profile_id)
                                                  绕过活跃指针
                                          空:   现状路径(活跃 profile)
                                                        ↓
                                         engine(单实例)
                                         store(profile_id=...)   ← 新参数
                                         recall(profile_id=...)  ← 已有
```

规则:

1. **空参数 = 现状路径**,含上游守卫逻辑位(对新语义不可达)。
2. **非空 = 纯路由**:不读不改 `ProfileRuntime` 的活跃指针与 `profile_generation`(验收直接断言不变)。
3. **校验前置**:daemon 先查 `profiles` 表,不存在立即 `success:false` + `error_code:"unknown_profile"`(建议 HTTP 404),不隐式创建,不触引擎。
4. **同一 DB 文件**:profile 是行级作用域,无新进程/模型;embedding worker、LLM backbone、reranker 共享不动。多实例方案的三条否决理由(N×500MB/端口碎片/global 断裂)天然不存在。

改动面:`tools_core.py`(2 个工具签名 + 透传)、`unified_daemon.py`(`/remember` `/recall` 参数与校验)、`engine.py`(store 签名与内部穿透)、`entity_channel.py`(缓存 LRU)。MCP `_daemon_proxy`/stdio 透传层为通用 kwargs/JSON 透传,预期零改动,实施时验证(含工具 schema 校验层接受新可选参数)。

## 4. 引擎层:store 穿透与缓存 LRU

`engine.store()` 内部对 `self._profile_id` 的使用分三类:

1. **纯数据传递点**(store_pipeline、admission、idempotency、pending journal):调用处换 `profile_id or self._profile_id`(与 recall 的 `pid` 惯例一致)。
2. **引擎状态读取点**(entity resolver 缓存、graph metrics 等预热组件):逐点审计,判定标准——profile 作用域的数据必须换参,跨 profile 共享的资源(embedder/LLM/reranker)不动。审计覆盖 `store`/`store_fact_direct`/`store_fast` 三条写路径调用树。
3. **异步后置点**(materializer/enrichment):journal 行自带 profile_id,materializer 按行补全,天然正确;实施时验证其不从引擎活跃指针反推 profile。

**邻接缓存 LRU**(entity_channel):单槽改 profile 键控 LRU——上限默认 3 槽(`SLM_ADJ_CACHE_PROFILES` 可调);缓存键沿用 `scope_key = (profile_id, include_global, include_shared)`;staleness 检查(边数/TTL)逐槽不变。目的唯一:消除交错 recall 整包重载抖动。

不做:retrieval 其它通道加缓存(本就无状态按参查库);动 `switch_profile`/`ProfileRuntime`(R6 维持现状);per-profile 引擎(已否决)。

## 5. 错误处理、并发与安全面

| 场景 | 行为 |
|---|---|
| profile_id 非空且不存在 | `success:false` + `error_code:"unknown_profile"` + 404,不隐式创建,不触引擎 |
| profile_id 空/None | 现状路径,无新错误面 |
| profile_id == 当前活跃 profile | 正常路由,不视为错误,不触发守卫 |
| profile 存在但正被 erase/迁移 | 沿用现有 storage 层错误,不新增拦截 |
| daemon 离线时 MCP 带 profile_id | 本进程引擎回落路径同样穿透,语义一致 |

**并发(R2)**:daemon 路由无共享可变状态(profile_id 请求局部);engine 既有锁照常串行化交错请求——串行但无交叉(单 SQLite + WAL + busy_timeout 为现网验证模式);LRU 自身加锁(沿用 entity_channel 锁惯例)。验收为清点式断言:双方各写 N 条按 profile 分组数数,零串库。

**安全(R5)**:allowlist 管工具不管参数,`remember,recall` 最小集天然支持;越权边界不变(能触到的 profile 集合仍为数据根全部 profile,不引入 profile 级 ACL);`_require_write_actor`、RBAC、loopback 全部在路由前照常生效。

## 6. 测试与验收

单测(`tests/test_server/test_per_request_profile.py` + engine 层扩充):

1. 路由正确性:带 doris 写读落 doris,zhihui 不可见(需求书验收 1)
2. 全局零副作用:操作前后 `/status` 的 `profile` 与 `profile_generation` 逐字段不变(验收 2,核心承诺单独成测)
3. unknown_profile:`success:false` + error_code,profiles 表行数不变(验收 5)
4. 兼容回归:不带参数落点与改前一致(验收 4;既有套件全绿 + 新增断言双层)
5. engine.store 穿透:目标 profile 的 fact 行正确;空参数回落活跃
6. 邻接 LRU:双 profile 交错第二次不触发重载;逐出后重载正确

集成:

7. 共享 daemon 双 profile 交错端到端(真实 daemon,两客户端交替写读,清点零交叉,generation 不动)
8. MCP stdio 通路:`SLM_MCP_TOOLS=remember,recall` 子进程携带 profile_id 全通(验收 6)

并发验收(验收 3):两线程各带不同 profile 并发写 N=50,分组清点零交叉无孤儿。

回归门:全量套件 + hermes 92(hermes 不带参数,是兼容性活体样本)。

## 7. hermes provider pin 适配(消费侧,本仓)

per-request 穿透落地后,本仓自有的消费方同步适配,消除"隐式跟全局指针"的残余耦合:

- **机制**:hermes provider(`integrations/hermes/__init__.py`)新增 `pin_profile` 配置,**默认开启**。开启时,provider 从其既有 MSLM profile 配置读取 profile 名(现有 `self._mslm_profile`),在**每个** daemon 路由的 recall/store 调用(7 个调用点,方案 A daemon-first 路由时接线)的 query/body 中携带 `profile_id`;daemon 离线回落本进程引擎时同样传参(`engine.recall` 已支持,`engine.store` 由本 spec §4 补齐)。
- **关闭时**:`pin_profile: false` 省略参数,provider 跟随活跃指针(保留"多人格复用同一 agent 进程、用 dashboard 切换"的既有能力)。
- **语义效果**:pin 开启后,知惠对任何其他客户端的 `switch_profile` 完全免疫;deepmaid 侧无需此开关(其架构每女仆一命名空间,无跟随语义,需求书 §6 直接全量携带)。
- **部署顺序约束**:pin 默认开启要求 daemon 先具备路由能力(同批发布,无窗口期问题);对旧 daemon 的行为退路——GET 未知 query 参数被忽略,POST body 的 `profile_id` 在 4.1.x 守卫下若与活跃不符会 409(显式失败优于静默改道,可接受)。
- **测试**:provider 单测断言 pin 开启时 daemon 调用含 `profile_id`、关闭时不含;既有 92 项套件回归(pin 默认值变化不得破坏现有断言,必要时以显式 pin=false 适配既有用例)。

## 8. 上游 PR 打包

- 叙事三支点:①引擎读路径上游已参数化,本 PR 补完写路径与 daemon/MCP 面;②409 守卫使命被路由取代("stale client must fail"→"writes correctly");③双客户端交错实测证据(知惠/Doris 即复现)。
- 邻接 LRU 为独立价值点,可拆单独 PR。
- 预期 review 焦点:materializer 的 profile 归属验证结论(写进 PR 描述);per-request 是否需要新 generation 字段(倾向不需要,`profile_generation` 保持只描述全局切换)。

## 9. 实施前置

- **Task 0:merge upstream(120 提交,4.1.11)**——守卫、`_runtime_profile`、`RememberRequest.profile_id` 均在缺口内,大分叉上做三层穿透冲突面不可控。merge 流程沿用 2026-08-21 的既定模式(策略表 + 存活核查 + 测试门)。

## 10. 后续(不在本 spec 范围)

- deepmaid 侧消费回归(需求书 §6:provider 显式携带 profile_id、删除 profile dance、交错契约用例——跨仓验收,依赖本仓先行落地)
- profile 级 ACL(独立需求)
- reranker daemon 路由、`test_enrich_new_facts_now` 守卫上游报告(既有 backlog)
