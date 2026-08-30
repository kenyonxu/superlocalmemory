# MSLM 需求书:per-request(或 per-connection)profile

> 日期:2026-08-30 · 提出方:deepmaid-agent(M1b 后续,M2 前置)
> 需求方向由主人拍板(2026-08-30):per-maid `SLM_DATA_DIR` 多实例方案**否决**(N 份 ~500MB embedding 常驻 + 端口分配 + global 范围碎片化),走单 daemon + per-request profile 路线。
> 本文件同步存放于 deepmaid-agent 与 superlocalmemory 两仓;**实现以本仓(superlocalmemory)为准**,deepmaid 侧消费验收见 §6。

## 1. 背景与问题

deepmaid-agent 是多女仆(maid)agent 平台,每个女仆一个记忆命名空间,映射为一个 MSLM profile(如 `doris`、`zhihui`)。host 侧(`@deepmaid/maid-memory-mslm`)per-maid 各持一个 `mslm mcp` stdio 子进程,经 MCP 调 `remember`/`recall`。

**问题:daemon 的活跃 profile 是全机单点有态。**

- `switch_profile` 同时改 daemon 与本进程引擎的全局 active profile;同一数据根的所有客户端共享这一个指针。
- 实测实景(2026-08-30,本机):知惠(hermes 现役,profile `zhihui`)与 Doris(deepmaid,profile `doris`)共存于 `~/.superlocalmemory` 的常驻 daemon(端口 8765)。任一客户端 `switch_profile` 后,另一客户端若不带显式 profile 的后续读写会被**静默改道**到新活跃 profile——**顺序交替即触发,不要求并发**。
- deepmaid 侧已做临时缓解(provider 每操作前 `get_status` 校验 + 漂移重切),但这是轮询防御:每个记忆操作多一次往返,且治标不治本——hermes(知惠)侧无此防御,仍会被 Doris 的切换改道。

## 2. 现状事实(2026-08-29/30 源码核实,superlocalmemory 本 fork)

- `switch_profile(profile_id)`:daemon 在跑时 POST `/api/profiles/{id}/switch`,ack 后同步本进程引擎(`tools_core.py`)。
- `get_status()`:daemon 在跑时读 daemon `/status`,返回 `{profile, mode, profile_generation, …}`。
- `remember`/`recall`:daemon 在跑时 write-through 路由到 daemon(daemon 不在时回落本进程引擎);**调用参数中无 profile 维度**(recall 有 `include_global`/`include_shared`,没有 profile_id)。
- 引擎内部(`engine._db` 各方法)已按 `profile_id` 参数化存储层——**缺的只是 API/daemon 层把 profile 穿透到请求**。
- daemon 数据根 ownership fail-closed(端口被异根 daemon 占据时拒绝跨根路由)。

## 3. 需求

### R1(核心):请求级 profile 穿透

`remember`、`recall`(以及 MCP 面上未来的读写类工具)接受**可选** `profile_id` 参数(或等价机制,如 MCP 会话头/HTTP header,实现方择优;参数优先,理由:对 stdio 与 HTTP 两种 transport 一视同仁):

- 携带 `profile_id` 时:该次操作的读写**只**作用于该 profile,**不读取也不变更** daemon 全局活跃 profile;
- 不携带时:维持现状(落到当前活跃 profile)——向后兼容,存量客户端(含 hermes)零改动零感知。

### R2:并发正确性

两个客户端各自携带不同 `profile_id` **并发**调用 `remember`/`recall`(或交替调用),结果必须各自落在自己的 profile,无交叉、无全局状态副作用。引擎层已按 profile 参数化,预期主要是 daemon 请求路径的去全局化。

### R3:profile 生命周期

- `profile_id` 指向不存在的 profile:`remember`/`recall` 返回显式错误(建议 `success:false` + 明确 error code,如 `unknown_profile`),**不隐式创建**;
- profile 创建仍走既有 CLI `mslm profile create`(host 侧已按此纪律实现)。

### R4:`get_status` 语义保持

- 不带参数:返回 daemon 全局态(现状,兼容);
- (可选,非必需)带 `profile_id`:返回该 profile 的 fact_count 等统计。deepmaid 消费面不依赖此可选项。

### R5:allowlist 与安全面

- 新参数不改变 `SLM_MCP_TOOLS` allowlist 机制;`remember,recall,get_status,switch_profile` 最小集即可使用全部新能力;
- per-request profile 不赋予调用方任何越权能力(与现状同界:能触到的 profile 集合不变)。

### R6:`switch_profile` 语义

维持现状(显式全局切换,供单客户端独占场景);deepmaid 在 R1 落地后将不再调用它(整个 profile dance 与 per-op 校验删除,见 §6)。

## 4. 方案取舍(已评估,供实现参考)

| 方案 | 结论 |
|---|---|
| **per-request profile**(R1 as-written) | **推荐终态**:无状态、真并行、单 daemon 单份 embedding 模型服务全部女仆;改动集中在 daemon 路由层 |
| per-connection profile 绑定(daemon 按连接记 profile) | 可接受的过渡:改动更小,恰好匹配 deepmaid 每女仆一个子进程连接的架构;但连接生命周期语义(重连、池回收)要定义清楚,且 hermes 进程内集成不走连接,覆盖不全 |
| per-maid `SLM_DATA_DIR` 多实例 | **否决**:N 份 ~500MB 常驻、端口手工分配、global 范围碎片化(知惠写的 global 记忆 Doris 看不见,破坏「认得主人」连续性通道) |

若实现方认为 per-connection 显著更省,可先交付 per-connection 并在响应中携带足够信息让 deepmaid 判断支持级别;per-request 仍是期望终态。

## 5. 验收场景(superlocalmemory 侧自测)

1. **基本穿透**:daemon 常驻;客户端甲带 `profile_id=doris` `remember` → `recall` 命中;客户端乙带 `profile_id=zhihui` `recall` 同查询不命中 doris 的事实;
2. **全局零副作用**:上述操作前后 `get_status()` 的 `profile` 与 `profile_generation` 不变;
3. **并发**:两客户端以不同 profile 交错/并发各写 N 条,落库清点零交叉;
4. **兼容**:不带 `profile_id` 的旧客户端行为逐字节不变(落当前活跃 profile);
5. **错误**:携带不存在 profile → 显式 `unknown_profile` 错误,无隐式创建;
6. **MCP stdio 通路**:`mslm mcp` 子进程经 `SLM_MCP_TOOLS` allowlist 调用携带 `profile_id` 的 remember/recall 全通。

## 6. deepmaid 侧消费与回归(实现完成后)

deepmaid 回归项(在 deepmaid-agent 仓执行):

1. `@deepmaid/maid-memory-mslm` provider:remember/recall 显式携带 `profile_id=命名空间`;删除 per-op `get_status` 校验与 profile dance(`switch_profile` 调用整体退役);
2. 契约套件 + 真子进程集成车道在共享 daemon 双 profile 交错场景下全绿(新增交错用例);
3. 实机:知惠与 Doris 同时对话,互不改道、互不串库(规格 §8 该行地雷关闭)。

## 7. 关联文档

- deepmaid 规格:[m1b-maid-memory-design-2026-08-28.md](m1b-maid-memory-design-2026-08-28.md) §8 风险表(daemon 全局态两行)
- 部署事实:[mslm-setup.md](mslm-setup.md)(工具面签名/daemon 路由/per-op 校验纪律)
