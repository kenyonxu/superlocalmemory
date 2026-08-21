# EmbeddingService daemon 通用路由(方案 B)设计 Spec

- 日期:2026-08-21
- 状态:设计已批准(四节设计逐节确认),待 spec 审阅
- 调研:`docs/research-2026-08-21-embeddingservice-daemon-routing.md`(根因、资产盘点、上游动态)
- 路线:fork 先行验证,稳定后作为第二个上游 PR(PR #118 已建立信誉)

## 1. 背景与目标

嵌入 worker 是有意的机器级单例(v3.4.13,`.embedding-worker.pid` 守卫)。daemon 持有 worker 时,任何嵌入式 FULL engine 宿主(gateway、MCP stdio server 等)的 `EmbeddingService` 检测到单例被持有后**自我禁用**(`_available=False` → `embed()` 返回 None),recall 的 semantic/hopfield/spreading_activation 三通道静默缺失。方案 A 已从 hermes provider 消费侧绕行修复;本设计在 slm 层修根因,使所有嵌入式消费方受益。

**目标**:`EmbeddingService` 在本地 worker 不可获得且 daemon 在线时,经 daemon 的 `/api/v3/embed`(v3.5.9 已存在)完成嵌入;daemon 不可达时行为与现状完全一致。

**非目标**:不新建/修改 daemon 端点;不改 `McpEmbedderProxy` 现有 LIGHT 接线;不动 reranker(后续独立 PR);不动 `compute_fisher_params`(本地纯数学)。

## 2. 已批准的关键决策

| 决策点 | 结论 |
|---|---|
| 认证模型 | 无凭据 loopback,与 v3.5.9 一致(不引入 capability token) |
| 触发条件 | 两类都触发:worker 被他进程持有(acquire 失败 / PID 存活)+ 内存压力跳过 |
| 对外可用性 | fallback 激活时 `is_available()` 报 `True`;`_is_remote_embedder()` 为 config 属性判定,对本地 config 返回 `False` → warm-guard 视为"可用且本地",store 同步嵌入经 HTTP,超预算自动降级 materializer;engine.py 零改动 |
| 默认开关 | 默认开启;`SLM_EMBED_DAEMON_FALLBACK=0` 一键回退 |

> 实施后修正(2026-08-21 最终评审):engine.py 的 `_warm_guard_embed` 读取的是原始属性 `_available`(engine.py:572-577),fallback 激活时 `_available is False`(§4"内部诚实"约束),因此 store 快路径的同步内联嵌入在 fallback 模式下**不启用**,写入嵌入由后台 materializer 异步兜底(daemon 侧 4.0.9 充实池双保险)。该行为严格更安全(写路径不承载 30s 级 HTTP)、严格优于修复前基线(materializer 此前拿到 None,现在经 daemon 成功),且是"engine.py 零改动"约束下的唯一一致解。上游 PR 叙事以此为准。

## 3. 架构与组件边界

核心改动集中在 `core/embeddings.py`,另有一个 proxy 类扩展点和 health 暴露点。

```
embed()/embed_batch()
  └─ _subprocess_embed()
       ├─ _available is False?
       │    ├─ _daemon_fallback 存在 → 委托 proxy ──► daemon POST /api/v3/embed
       │    └─ 否则 → 返回 None(现状)
       └─ 正常 → 本地 worker(stdin/stdout)
```

### 3.1 DaemonEmbedderProxy(proxy 能力扩展)

扩展 `core/mcp_embedder_proxy.py` 的 `McpEmbedderProxy`(加构造参数,不新增类):

- `timeout`:构造默认值**保持 5s 不变**(LIGHT 路径 `McpEmbedderProxy(port=port)` 行为零变化);`EmbeddingService` attach fallback 时显式传 30s,读取顺序为 `SLM_EMBED_DAEMON_TIMEOUT` 环境变量 → 默认 30s。
- `strict: bool`:为 `True` 时 `embed_batch()` 失败**抛出异常**而非静默返回 None 列表——`EmbeddingService` 需要区分"daemon 死了"与"嵌入失败"以驱动失败计数。默认 `False`,LIGHT 路径行为不变。
- `is_available()` 缓存策略:否定结果不缓存(daemon 可能后启动);肯定结果保留缓存(现状)。

接口与现有 embedder 协议对齐:`embed(text) -> list[float] | None`、`embed_batch(texts) -> list[list[float] | None]`、`is_available() -> bool`。

### 3.2 EmbeddingService._daemon_fallback

- 新属性 `self._daemon_fallback: McpEmbedderProxy | None`(init 为 `None`)+ `self._fallback_fail_count: int` + `self._fallback_served: int`。
- attach 逻辑:仅在 `_ensure_worker()` 的两个单例分支(acquire 失败、PID 存活)与内存压力分支内、设置 `_available=False` 之前调用;`SLM_EMBED_DAEMON_FALLBACK=0` 时直接跳过。
- detach 逻辑:失败计数达阈值(见 §5)后置 `_daemon_fallback=None`,回到现状行为。
- `_subprocess_embed()` 的 `_available is False` 短路处:先查 `_daemon_fallback`,存在则委托并返回。

### 3.3 无改动的部分

`engine.py`(LIGHT `_try_init_proxy` 与 FULL `_init_heavy_layer` 各自独立)、daemon 端点、`compute_fisher_params`、reranker。

## 4. 状态机与降级语义

`_subprocess_embed()` 每次调用的判定顺序:

1. 本地 worker 活着可用 → 本地嵌入(最高优先级,零 HTTP 开销)
2. `_available is False` + fallback 活 → daemon proxy 嵌入
3. `_available is False` + 无 fallback → 返回 None(现状,不劣化)
4. `_available is None`(recall_health 重探测信号)→ fall through 重试 spawn,proxy **不参与**(历史事故防线,见 embeddings.py:462-466 注释)

语义规则:

- **内部诚实,对外可用**:attach 不改变 `_available=False` 的内部事实(本地 worker 确实不可用);`is_available()` 在 fallback 存在时返回 `True`。
- **优先级单调收敛**:本地 worker > proxy > None。daemon 死亡且 worker PID 失效后,下次 `_ensure_worker()` 走正常 spawn,本地复活,fallback 弃用。
- **重试节奏**:不做指数退避;recall_health 的 heal 周期触发 `_ensure_worker()` 时重新尝试 attach。
- **维度校验**:proxy 返回向量照常过 `_validate_dimension()`;不匹配抛 `DimensionMismatchError`,消息含 "via daemon fallback"。
- **`is_warm`**:fallback 成功服务 ≥1 次(`_fallback_served > 0`)后返回 `True`,避免 background enrichment 误判冷嵌入。
- **`unload()`/shutdown**:对 proxy 为 no-op,不影响 daemon 侧。
- **store 快路径双保险**:fallback 模式下 gateway 侧同步内联嵌入**不启用**(warm-guard 读原始属性 `_available`,fallback 时为 `False`,见 §2 实施后修正),写入嵌入由后台 materializer 异步兜底;daemon 侧 4.0.9 `_enrich_and_release` 充实池亦会兜底。

## 5. 错误处理、超时与可观测性

超时分层:

| 路径 | 超时 | 备注 |
|---|---|---|
| proxy ping | 2s(沿用 v3.5.9) | attach 判定不拖慢 `_ensure_worker` |
| proxy `embed_batch()` | 30s 默认,`SLM_EMBED_DAEMON_TIMEOUT` 可配 | 冷 worker 首调可能超时;超时处理见下 |
| daemon 侧 embed | 沿用 daemon 现有机制 | 不改 daemon |

失败分类与计数(detach 阈值 = 3):

- 连接拒绝/连接超时 → 计 1 次(daemon 大概率不在)
- 读超时(30s) → 首次宽容(可能冷启),连续 2 次才计 1 次
- HTTP 5xx / 响应畸形 → 计 1 次(daemon 在但 embedder 坏)
- 维度不匹配 → 不计数,立即抛 `DimensionMismatchError`(配置错误,重试不自愈)

可观测性:

- attach 成功:一次性 `logger.info`(含 daemon port)
- detach:`logger.warning` 带原因与计数
- health/组件注册表暴露 `embedder_mode: local | daemon-fallback | unavailable`
- proxy 每次调用 debug 级日志(与 v3.5.9 一致)

并发:proxy 调用与本地 worker 调用共用 `_request_lock()`(串行语义一致);daemon 侧端点本在 executor 中运行,不受调用方串行影响。

## 6. 测试策略

单测 `tests/test_core/test_embedding_daemon_fallback.py`(新):

1. 单例持有 + daemon 在线 → `embed()` 走 proxy;`_available is False`(内部);`is_available() is True`(对外)
2. 单例持有 + daemon 离线 → 返回 None,无异常,与现状一致
3. 内存压力触发 + daemon 在线 → 走 fallback
4. 三态回归:`_available=None` 时不走 proxy 短路,fall through 重试 spawn
5. 失败计数:连续 3 次连接拒绝 → detach,后续返回 None;再次 `_ensure_worker()` 触发重新 attach
6. 维度不匹配 → 抛 `DimensionMismatchError` 且消息含 "daemon fallback"
7. `is_warm`/`unload`:fallback 服务后 `is_warm is True`;`unload()` no-op 不炸

集成 `tests/test_integration/test_embedding_fallback_two_process.py`(新):

8. 双进程实证:进程 A 持 worker 单例(PID 文件);进程 B 起 FULL engine + 微型 HTTP app(uvicorn,返回固定维度向量)充当 daemon → 断言 B 的 recall semantic 通道有分数、零 embedding-None 警告。用真实 HTTP 服务而非 mock,覆盖序列化路径。

性能:不设专门 benchmark(fallback 只在本地不可用时激活,严格优于零通道);store 超预算自降级由 warm-guard 既有机制兜底。

回归基线:全量套件(9758 基准)+ hermes 92,全绿。

## 7. 工作量与上游化

预估 3 天:fallback 分支 + 边界 0.5d;`is_warm`/health/日志 0.5d;单测 7 项 1d;双进程集成 0.5d;上游 PR 材料 0.5d。

上游叙事:v3.5.9 模式的自然延伸("FULL engine 单例竞争失败 → 同一 proxy"),diff 小、端点零新增;证据链完整(生产拓扑 4 个月缺通道 + 方案 A 联调数据)。预期 review 焦点:三态语义保持、认证模型选择(已按 v3.5.9 对齐)、`is_warm` 对 enrichment 调度的影响。

## 8. 后续(不在本 spec 范围)

- reranker 同模式路由 + daemon rerank 端点(独立 PR)
- `test_enrich_new_facts_now` 缺 skip 守卫的上游 bug 报告
