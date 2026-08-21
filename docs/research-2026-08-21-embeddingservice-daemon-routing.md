# 方案 B 调研:EmbeddingService daemon 通用路由

- 日期:2026-08-21
- 状态:调研完成,待立项决策
- 前置文档:`upgrade-assessment-2026-08-13-v4-merge-hermes.md` §9(嵌入式进程召回质量:daemon 路由)
- 关联:PR #118 已落地(4.0.6,co-author);本项是路线图中的"下一个上游贡献候选"

---

## 1. 结论(TL;DR)

方案 B 的前置假设已经过时——**daemon 侧纯 embed 端点已存在**,上游 v3.5.9 就 shipped 了:

- `core/mcp_embedder_proxy.py`:`McpEmbedderProxy`,把 `embed()`/`embed_batch()` 通过 localhost HTTP 委托给 daemon
- `server/routes/v3_api.py:543-585`:`GET /api/v3/embed/ping` + `POST /api/v3/embed`,后者在 executor 里调 `engine._embedder.embed_batch(texts)`

因此方案 B 的工作量比 §9.3 预估的"中"明显降低:**不需要新建 daemon 端点,也不需要复用 WorkerPool IPC**,核心工作是把"单例竞争失败 → 自我禁用"改为"单例竞争失败 → 降级为 daemon 代理",复用 v3.5.9 已建立的 proxy 模式。预估降为**小-中工作量**(主要是边界情况处理 + 测试)。

另有一项**事实修正**:§9.1 称"reranker 遇到同样单例时是路由到 daemon 的 worker",与当前代码不符——reranker 同样没有路由,只是降级更温和(退回 fusion 分数)。详见 §4。

---

## 2. 根因路径(已核实)

### 2.1 单例守卫与自我禁用

嵌入 worker 是有意的机器级单例(2026-04-07 内存爆炸事故,v3.4.13 引入 `.embedding-worker.pid` + flock 双重守卫):

`core/embeddings.py` `EmbeddingService._ensure_worker()`(约 700-731 行):

```
acquire_embedding_lock() 失败        → self._available = False  # 他进程持有
_is_embedding_worker_alive() 为真    → release + self._available = False
_check_memory_pressure() 不足        → release + self._available = False
```

三态约定(重要,改动必须遵守):

- `_available = True`:worker 可用
- `_available = False`:**终态禁用**,`_subprocess_embed()` 直接短路返回 None(embeddings.py:464)
- `_available = None`:recall-health 自愈的"重新探测"信号,必须 fall through 重试 spawn(注释明确警告:用 `not self._available` 判断曾在首个 heal tick 把本地 worker 打砖)

### 2.2 禁用后的召回影响

`_subprocess_embed()` 返回 None → `embed()` 返回 None / `embed_batch()` 返回 `[None]*n` → recall 的 semantic / hopfield / spreading_activation 三通道静默跳过,只剩 BM25 / entity / temporal / profile。

典型拓扑:unified_daemon(FULL engine,持有 worker)+ gateway / MCP / 任意嵌入式宿主(FULL engine,竞争失败,自我禁用)。§9.1 已查实知惠自 2026 年 4 月起召回缺三通道,方案 A(provider 侧 daemon-first 路由)已从消费侧修复;本方案是从 slm 层修根因。

---

## 3. 现有可复用资产(v3.5.9,本次调研的关键发现)

### 3.1 McpEmbedderProxy

`core/mcp_embedder_proxy.py`,为修复 PR #30(MCP LIGHT 进程存 NULL embedding)而引入:

- 接口与 `EmbeddingService`/`OllamaEmbedder` 对齐:`embed()`、`embed_batch()`、`compute_fisher_params()`(后者返回 `(None, None)`,由 daemon 的 consolidation 异步补齐)
- `is_available()`:ping `/api/v3/embed/ping`,首次成功后缓存
- 默认超时 5s(为 MCP 内联 store 设计);失败返回 `[None]*n`,**不抛异常**

### 3.2 daemon 端点

`server/routes/v3_api.py`:

- `GET /api/v3/embed/ping`:liveness,恒 200(不检查 engine 状态,弱信号)
- `POST /api/v3/embed`:`{"texts": [...]}` → daemon FULL engine 的 `embed_batch()`,在 executor 中运行避免阻塞事件循环;engine/embedder 缺失时 503
- 认证:proxy 侧为**无凭据纯 httpx 调用**,依赖 daemon 绑定 loopback。与 `cli.daemon.daemon_request`(capability token)是两套访问模型

### 3.3 当前接线缺口

`core/engine.py:177-182`:

```python
if self._capabilities is Capabilities.FULL:
    self._init_heavy_layer()      # 本地 EmbeddingService,无 fallback
else:
    ...
    self._try_init_proxy()        # LIGHT:daemon 可达则挂 McpEmbedderProxy
```

**proxy 只接给 LIGHT engine。FULL engine 单例竞争失败时没有任何降级路径**——这就是方案 B 要补的口子。

---

## 4. 事实修正:reranker 并没有路由

§9.1 的对比描述("跨 encoder reranker 遇到同样单例时是路由到 daemon 的 worker")与代码不符:

- `retrieval/reranker.py:_ensure_worker()`(约 314-317 行):PID 文件显示 worker 存活时直接 `return`,`_worker_proc` 保持 None
- `_send_request()`:`_worker_proc is None` → 返回 None → 调用方退回 fusion 分数
- warmup 注释"this instance uses it on demand"(reranker.py:184-187)是误导——worker 的 stdin/stdout 管道属于 spawn 它的进程,兄弟进程无法使用它

即 reranker 与 embedder 是**同一缺口**,只是后果更轻(丢 rerank 精度,不丢整个召回通道)。方案 B 落地后,reranker 可用同模式做后续跟进(需新增 daemon rerank 端点,不在本方案范围)。

§9.1 的描述可能源自方案 A 实施后的混合记忆——provider 把整个 recall 路由给 daemon 后,daemon 侧的 reranker 自然生效。

---

## 5. 设计方案

### 5.1 推荐:B1 — EmbeddingService 内嵌 daemon 降级

在 `EmbeddingService` 内部加一层 fallback:仅当"单例被他进程持有"导致无法 spawn 时,降级为 daemon 代理。

改动点(`core/embeddings.py`):

1. `_ensure_worker()` 的两处单例竞争分支(acquire 失败 / PID 存活)在设 `_available=False` **之前**,先尝试 attach daemon fallback:
   - 构造轻量 proxy(复用 `McpEmbedderProxy`,或抽公共基类)
   - proxy `is_available()` → 置 `self._daemon_fallback = proxy`,`_available` 保持三态语义不变(False 仍表示"本地 worker 终态禁用")
   - proxy 不可达 → 维持现状(`_available=False`,静默降级)
2. `_subprocess_embed()` 的 `self._available is False` 短路处(embeddings.py:464):先查 `self._daemon_fallback`,有则委托并返回
3. 内存压力分支(第三处)也建议走同一 fallback——daemon 已有 worker 时,本地内存压力不构成拒绝理由

关键边界:

- **失败归因**:仅"worker 被他进程持有"和"内存压力"触发 fallback;spawn 崩溃、通信超时等维持现有语义(避免把真故障掩盖成慢故障)
- **超时**:proxy 默认 5s 对 daemon 冷 worker(ONNX 冷启 30-60s)太短。fallback 场景建议 30s 可配(`SLM_EMBED_DAEMON_TIMEOUT`),或复用 `_SUBPROCESS_RESPONSE_TIMEOUT`
- **daemon 死亡抖动**:proxy 调用失败(连接拒绝)→ 单次返回 None,并清缓存重新探测;若 PID 文件已失效,下次 `_ensure_worker` 会走正常 spawn——**本地 spawn 与 daemon fallback 互为备份**,优先级:本地 worker > daemon proxy > None
- **维度校验**:`_validate_dimension()` 在 proxy 返回后照常执行;daemon 与本地配置模型不一致时会抛 `DimensionMismatchError`——同机同 profile 下不常见,但报错信息应点名"daemon fallback 维度不匹配",避免误导排查方向
- **`is_warm` 语义**:当前 `is_warm` 依赖 `_worker_proc` + `_request_count`,proxy 路径下恒 False,会让 background enrichment 误判 embedder 冷。需让 `is_warm` 在 daemon fallback 已服务过请求时返回 True
- **health/observability**:fallback 激活应打一条 info 日志(一次性),并在 health 输出中体现("embedder: daemon-proxy"),否则三通道恢复是静默的,排障时无法区分
- **fisher params**:本地纯数学,不受影响
- **认证**:与 v3.5.9 保持一致(无凭据 loopback)是最小 diff;若要向 `daemon_request` 的 capability token 模型靠拢,改动面会扩大,建议留给上游 review 决定
- **opt-out**:保留环境变量开关(如 `SLM_EMBED_DAEMON_FALLBACK=0`),出问题时一行回退

### 5.2 备选:B2 — engine 层组合 embedder

在 `engine_wiring.init_embedder()` 返回 `CompositeEmbedder(primary=EmbeddingService, fallback=McpEmbedderProxy)`。优点是 EmbeddingService 零改动;缺点是 fallback 判定逻辑移到组合层,拿不到"为什么本地不可用"的内部状态,容易把 spawn 崩溃也降级掉。不推荐。

### 5.3 不做的事

- 不新建 daemon embed 端点(已存在)
- 不动 WorkerPool IPC(recall_worker 子进程模式与 embed 委托无关,复用它是错配)
- 不改 reranker(后续跟进项)
- 不动 LIGHT 模式的现有 proxy 接线

---

## 6. 测试计划

1. **单测:单例持有 + daemon 在线** → mock `_is_embedding_worker_alive()=True` + proxy `is_available()=True`,断言 `embed()` 走 proxy 且返回向量、`_available` 语义不变
2. **单测:单例持有 + daemon 离线** → 断言返回 None(现状等价),无异常
3. **单测:daemon 中途死亡** → proxy 首次调用失败后清缓存,下次重新探测;PID 文件失效后本地 spawn 恢复
4. **单测:维度不匹配** → proxy 返回错误维度向量,断言抛 `DimensionMismatchError` 且信息含 daemon fallback 字样
5. **单测:三态回归** → `_available=None`(heal 重探测)不触发 proxy 短路
6. **单测:is_warm** → fallback 服务过请求后为 True
7. **集成:双进程实证** → 进程 A 起 FULL engine 持有 worker;进程 B 起 FULL engine,断言 recall 三通道有分数、无 embedding-None 警告(复刻 §9.2 联调实证形态,但在 slm 层)
8. **并发:多线程 embed_batch 经 fallback** → 无交错、无泄漏线程

---

## 7. 上游适配性评估

- **叙事契合度高**:v3.5.9 已经确立了"MCP LIGHT → daemon proxy"模式,本方案是它的自然延伸("FULL engine 单例竞争失败 → 同一 proxy"),不是新范式
- **diff 小**:核心改动集中在 `embeddings.py` 一个文件 + 测试;端点零新增
- **证据充分**:知惠生产拓扑(2026 年 4 月起三通道缺失)+ §9.2 方案 A 的联调数据(score 0.647/conf 0.900 全通道)可作 before/after 佐证
- **PR #118 信誉已建立**(4.0.6 shipped,co-author),按路线图此刻正是提交窗口
- **可能的 review 焦点**(预演):三态语义不能被破坏(有历史事故注释);认证模型是否要向 capability token 靠;`is_warm` 改动对 background enrichment 调度的影响

---

## 7.1 上游动态核查(2026-08-21,4.0.7–4.0.9)

对 `upstream/main` 逐文件核查后的结论:

- **缺口仍未被填**:`embeddings.py`、`mcp_embedder_proxy.py` 自我方 merge-base 起零改动;`_try_init_proxy()` 依然只接 LIGHT engine;`/api/v3/embed(+ping)` 端点仍在(v3_api.py:733/747)。方案 B 的空间没有被上游占掉。
- **reranker 无路由动向**:上游仅改了 fallback 排序的稳定性(tie-break by `fact_id`),没有引入任何 daemon 路由。
- **⚠️ 一处新增耦合,方案 B 必须兼容**:4.0.7–4.0.9 在 store 快路径引入 `_warm_guard_embed`(engine.py)——只有当 `_embedder._available is True` 且为本地 embedder 时才同步内联嵌入,否则推迟给后台 materializer 补。daemon fallback 激活时本地 `_available` 恰为 False,store 会走异步补全路径。这在功能上可接受(materializer 会补),但设计文档 §5.1 的 `_available` 三态语义需要明确选择:
  1. 让 fallback 激活时 `_available` 对外报 True(store 同步嵌入经 HTTP,延迟略增,但召回/写入路径行为与本地一致);
  2. 保持 False(store 退回异步补全,语义最诚实,但"写入即可搜"依赖 materializer 及时性,与 4.0.9 "findable the moment you write it" 的叙事相左)。
  倾向方案 1,把"是否本地"与"是否可用"拆成两个信号(`_available` 报可用性,warm-guard 已有的 `_is_remote_embedder()` 判断 locality——若 proxy fallback 被它识别为 remote,则自动走异步,反而免费得到方案 2 的行为。实现时验证 `_is_remote_embedder` 对 fallback 形态的判定)。
- `worker_pool.py` 仅新增时序召回参数(as_of 等),与本方案无关。
- 上游已发 4.0.7/4.0.8/4.0.9,我方落后三个 release;实施前需要先 merge upstream(注意 engine.py 有 444 行 diff,`_warm_guard_embed` 与 store 快路径是合并时的语义重点)。

---

## 8. 工作量与里程碑

| 项 | 预估 |
|---|---|
| `embeddings.py` fallback 分支 + 边界处理 | 0.5 天 |
| `is_warm`/health/日志语义 | 0.5 天 |
| 单测 6 项 | 1 天 |
| 双进程集成实证 | 0.5 天 |
| 上游 PR 材料(CHANGELOG、行为说明、before/after 数据) | 0.5 天 |

合计约 3 天(原 §9.3 预估"中工作量"偏保守,因端点已存在而降档)。

**遗留跟进项(单独 PR)**:reranker 同模式路由 + daemon rerank 端点。

---

## 9. 待决策

1. 是否立项实施?(本调研建议:做,fork 先行验证,稳定后作为第二个上游 PR)
2. 认证模型:跟随 v3.5.9 无凭据 loopback(最小 diff)还是直接上 capability token?
3. 内存压力分支是否纳入 fallback 触发条件(本调研建议:纳入)
