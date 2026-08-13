# 升级前总体评估：合并上游 V4 + Hermes 集成影响分析

**日期**:2026-08-13
**分析对象**:本地 `main`(mslm-memory v4.1.1,基于 v3.6.22 + 19 个本地提交)→ 上游 `upstream/main`(SuperLocalMemory v4.0.1)
**方法**:`git merge-tree --write-tree` 真实合并预览 + 逐文件语义比对 + Hermes 集成 API 依赖逐一核对

---

## 1. 总体结论(TL;DR)

| 维度 | 结论 |
|------|------|
| **文本冲突面** | 很小 — 仅 3 个文件冲突:`README.md`、`package.json`、`pyproject.toml` |
| **语义风险面** | 中等 — 3 个核心文件(config/embeddings/engine_wiring)文本自动合并成功,但上游分别改写了 629/539/202 行,本地补丁必须重新验证落点 |
| **Hermes 集成兼容性** | 高 — 依赖的全部 9 个引擎/配置 API 在 V4 中签名兼容(超集),无需改代码即可编译通过 |
| **必须保留的本地补丁** | 4 项,上游均未采纳(见 §4) |
| **数据安全** | ⚠️ M033–M039 共 7 个新迁移自动执行,schema 只进不退,升级前**必须备份** `~/.superlocalmemory/` |
| **推荐策略** | **merge(非 rebase)**,一次解决冲突;理由见 §6 |

---

## 2. 变更规模

- 分叉点:`62f1cf82`(v3.6.22 文档提交)
- 上游前进:**372 个提交**,1364 个文件,+191,544 / -36,586 行
- 版本线:3.7.x → 3.8.1~3.8.14(Windows/WSL2 可靠性、learning-signal 路由、远程 reranker)→ **4.0.0(可验证记忆事务)** → 4.0.1(dashboard/ops-status 修正)
- 本地前进:19 个提交 = mslm 品牌改造 + Hermes MemoryProvider 全套 + 3 个核心补丁 + 大量文档

---

## 3. 冲突面评估(merge-tree 实测)

### 3.1 真实文本冲突(3 个文件)

| 文件 | 冲突双方 | 处置建议 |
|------|----------|----------|
| `pyproject.toml` | 本地:`name="mslm-memory"`, version 4.1.1, `torch>=2.11.0`<br>上游:version 4.0.1, `torch==2.11.0`, mcp 2.0.0, transformers 5.5.4, huggingface_hub 1.5.0, cryptography 50.0.0, fastapi 0.139.2, license 格式现代化 | **以上游为基底**,重贴本地品牌(name/description)与 torch 宽松 pin;版本号见 §5.4 |
| `package.json` | 本地:mslm-memory 品牌 + mslm/multi-scope 关键词<br>上游:版本/依赖更新 | 同上,以上游为基底重贴品牌 |
| `README.md` | 本地:mslm 品牌 + Hermes 主推文档结构<br>上游:V4 全面重写 | 以上游为基底,重贴品牌段落与 Hermes 章节;或保留本地版并在顶部加 V4 更新说明 |

### 3.2 文本自动合并但需语义重验证(3 个核心文件)

| 文件 | 上游改动量 | 本地补丁 | 风险 |
|------|-----------|----------|------|
| `core/config.py` | +629 行(大改写) | `for_mode()` 保留 `ollama_model`/`ollama_base_url` | **中** — 上游 `for_mode` 已重构(多处 `EmbeddingConfig(...)` 构造),补丁虽落上但需确认覆盖所有构造分支 |
| `core/embeddings.py` | +539 行(大改写) | 代理转发 + `HF_ENDPOINT` 移除 + 超时 180→300s | **中** — 需确认 worker 子进程 env 构造块在上游新版中形态未变 |
| `core/engine_wiring.py` | +202 行 | proxy 参数透传到 `EmbeddingService` | **低-中** — 上游 `EmbeddingService.__init__` 签名仍是 `(self, config)`,本地 kwargs 是新增参数,兼容 |

### 3.3 零冲突区(纯新增)

- `src/superlocalmemory/integrations/hermes/` 全套(上游**不存在** `integrations/` 目录)— 9 个文件全部干净落地
- `docs/` 下本地新增文档(hermes-agent-guide、multi-scope-memory、mslm 文档套件等)自动合并
- `docs/slm/` 截图等二进制资源无冲突

---

## 4. 必须保留的本地补丁(上游未采纳)

逐一核实上游 v4.0.1 源码后的结论:

| # | 补丁 | 上游状态 | 证据 |
|---|------|----------|------|
| 1 | `for_mode()` 保留 ollama 配置(`8ea07aa`) | ❌ 未修 — 上游 `for_mode` 内 `ollama_model=` 出现次数为 **0** | `config.py` grep |
| 2 | embeddings worker 代理转发 + `HF_ENDPOINT` 移除(`ab17c78`) | ❌ 上游 embeddings.py 中 `proxy`/`HF_ENDPOINT` **零匹配** | `embeddings.py` grep |
| 3 | `engine_wiring` proxy 透传(`ab17c78`) | ❌ 无对应逻辑 | `engine_wiring.py` diff |
| 4 | `torch>=2.11.0` 宽松 pin(`c1312f8`) | ❌ 上游仍 `torch==2.11.0`(两处,75 行与 107 行) | pyproject |

**结论**:这 4 项是本地环境(中国大陆网络、Ollama 用户、torch 版本兼容)的刚性需求,合并后必须逐一确认存活,并补充回归测试。

---

## 5. V4 对 Hermes 集成的影响分析

### 5.1 API 兼容矩阵(全部 ✅)

| Hermes 调用点 | V4 状态 | 说明 |
|---------------|---------|------|
| `engine.store(content, session_id=, speaker=, scope=, shared_with=)` | ✅ 完全兼容 | V4 签名为超集(新增 `session_date`/`role`/`metadata` 可选参数) |
| `engine.recall(query, limit=, fast=, include_global=, include_shared=)` | ✅ 兼容 | `fast` 语义变化见 5.2-c |
| `RecallResponse.query` / `.results` | ✅ 保留 | 字段完整 |
| `RetrievalResult.score` / `.confidence` | ⚠️ 兼容但有弃用路径 | Score Contract v2:二者成为 `relevance_score`/`memory_confidence` 的**单版本别名**,下个大版本可能移除 |
| `engine.initialize()` / `close_session()` / `.db` | ✅ 保留 | — |
| `engine.create_speaker_entities("user", "hermes")` | ✅ 保留 | engine.py:777 |
| `SLMConfig.load()` / `.active_profile` / `.mode` / `.base_dir` / `.embedding` | ✅ 保留 | — |
| `Mode[mode_override]`(A/B/C 枚举) | ✅ 保留 | models.py:75 |

**未提交的 1 行改动**(prefetch 中移除 `fast=True`)**恰好与 V4 语义对齐**,应随合并一起提交。

### 5.2 行为变化(Hermes 集成需知)

**a. 写入路径走 V4 准入网关(透明,但值得了解)**
V4 `store()` 内部转调 `canonical_store(trusted_actor_id=local_trusted_actor_id("python-api"))`。嵌入式 Python 调用是默认可信 actor,Hermes 集成**无需任何适配**。但每次写入现在会产生持久化回执 + 完成清单(receipt/manifest),磁盘开销略增。

**b. 写入前 secrets 清洗**
V4 在 canonical 写入路径和导入路径上都会清洗疑似密钥内容。Hermes 的 `sync_turn`/`on_memory_write` 转存的对话内容若包含 API key 形态字符串,**会在持久化前被改写**。这是期望行为(安全增强),但需在文档中告知用户。

**c. `recall` 的 `fast` 参数语义重构**
`fast=None`(新默认)= client-driven-agentic 策略:热路径跳过内部 agentic 验证轮(等价于旧 `fast=True`),由调用方 LLM 驱动查询精炼;`fast=False` = 强制内部验证轮。Hermes 作为"智能客户端"应使用默认值,**不应再显式传 `fast=True`**(未提交的改动正是如此,✅)。

**d. 多 scope 检索默认关闭 ⚠️**
V4 中 `include_global=None`/`include_shared=None` 时回落到 `ScopeConfig` 默认值,**出厂为 OFF**。Hermes 集成的默认值是 `include_global=True`、`include_shared=False`(`__init__.py:264-268`,显式传递),因此行为不变;但若用户在 hermes 配置中显式关闭了 include_global,升级后不会有意外开启。无需改动,建议加一条集成测试锁定该行为。

**e. M033–M039 共 7 个新迁移自动执行 ⚠️**
启动时自动应用,forward-only,**不支持 schema 降级**(官方建议:恢复升级前备份)。涉及 projection transactions、obligation integrity、erasure receipts、vector row map、manifest HMAC、learning feedback channel、scene fact members。升级前必须备份 `~/.superlocalmemory/` 整个目录。

**f. MCP SDK 2.0 全无状态化(间接影响)**
Hermes MemoryProvider 路径**不经过 MCP**,无直接影响。但 `slm mcp` 用户侧:FastMCP→MCPServer 重写、默认 stateless Streamable HTTP(`SLM_MCP_STATEFUL=1` 退回)。若 hermes-agent 环境中另有 MCP 客户端连接 slm,需回归测试。

**g. 依赖大版本跳跃(部署风险)**

| 依赖 | 旧 | 新 | 影响面 |
|------|----|----|--------|
| mcp | 1.27.1 | 2.0.0 | MCP server 重写(间接) |
| transformers | 4.57.6 | **5.5.4** | ⚠️ 大版本跳,embedding worker 模型加载路径需实测 |
| huggingface_hub | 0.36.2 | 1.5.0 | 同上,API 有破坏性变更历史 |
| fastapi[all] | 0.136.1 | 0.139.2 | dashboard(低) |
| cryptography | 45.0.2 | 50.0.0 | 审计链/清单 HMAC(中) |
| pywin32 | — | 311(win32) | 仅 Windows |

升级后必须**重建虚拟环境**并实测 embedding worker 冷启动(本地代理补丁 + transformers 5.x 的组合是最高风险点)。

### 5.3 风险矩阵

| 风险 | 等级 | 缓解 |
|------|------|------|
| 本地 ollama/proxy 补丁在大改写后的文件中语义失效 | 🟡 中 | 合并后逐行 review 3 个文件补丁落点 + 新增回归测试 |
| transformers 5.5.4 与 embedding worker 不兼容 | 🟡 中 | 升级后第一优先级实测 `slm doctor` + 冷启动 embed |
| 数据迁移 M033–M039 失败或产生不可用状态 | 🔴 高(数据) | 升级前完整备份;先在副本数据目录上试运行 `slm status` |
| `RetrievalResult.score/confidence` 别名未来移除 | 🟢 低 | 本次可在 hermes `_format_recall_results` 中迁移到新字段名 |
| secrets 清洗改写用户期望存储的内容 | 🟢 低 | 文档说明;属安全增强 |
| 品牌与上游 README/pyproject 反复冲突(未来每次 sync) | 🟡 中(流程) | 考虑将品牌层外移(如 `pyproject.brand.toml` 或打包脚本注入),减少未来冲突面 |

### 5.4 版本号策略

本地面向用户已发布 4.1.1(高于上游 4.0.1)。合并后建议:
- **本地版本定为 4.2.0**(mslm 线:4.1.x = 基于 V3 内核,4.2.x = 基于 V4 内核),保持单调递增且语义清晰;
- `plugin.yaml` 中 `mslm-memory>=4.1.0` 可放宽为 `>=4.2.0` 以锁定 V4 内核行为。

---

### 5.5 对 2026-08-13 SQLite 全局互斥锁死锁(postmortem)的影响

**结论:V4 提供概率级缓解,根因未修。Option A/B 仍需合并后本地实现。**

对照 postmortem 根因链逐环核实(`upstream/main` 实测):

| 根因环节 | V4 状态 |
|----------|---------|
| WAL close 需 checkpoint,可被长寿命 daemon 的 reader mark 无限期 pin | ❌ 未修 — 无 `SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`(全库零匹配);learning/recall_queue 仍用缓存长连接,daemon 侧 pinning 机制不变 |
| close 路径持进程全局 VFS mutex → 所有 `sqlite3.connect()` 排队 | ❌ 未修 — `DatabaseManager` 仍是 per-call 连接模型(`close()` 显式标注 "No-op for per-call connection model"),每次 execute 后照常 close |
| WAL 硬编码、无配置开关 | ❌ 未修且**更多** — 调用点从 ~10 增至 ~20;`database.py:182` 仍无条件 `journal_mode=WAL`,且 `_enable_wal()` 在 `__init__` 中调用 → **手动切 DELETE 仍会在 engine 初始化时被改回**(postmortem 记录的"daemon 强制改回 6 个库"行为在 V4 依然成立,只是不再二次 re-assert) |

V4 带来的概率级改善(v3.8.4 `85238e70` / `5165686f` 等):

1. **`wal_autocheckpoint=400` + `synchronous=NORMAL`** — checkpoint 更小更频,close 时的 checkpoint 积压工作量大幅下降(对死锁窗口最直接的压缩)
2. **recall 只读热路径** — recall 不再做同步簿记写入,嵌入宿主(hermes)进程内 WAL 写连接数大幅减少
3. **写路径单写锁 + RLock + busy_timeout=10s 全覆盖** — 消除写方锁风暴;注意 busy_timeout 对 close 路径的 checkpoint 等待**不生效**(本次事故 20 分钟无超时即是证明),该缺口 V4 未补
4. **daemon 不再 re-assert WAL**(H-CONC-3)+ "live daemon 下不得 flip journal mode" 约定(`reward.py` M-P-02)— 消除 journal-mode 切换争用这个二次故障类(对应 hermes post-merge fix log)
5. **可观测性** — M031 死信表 + V4 回执/清单:卡死的 slm 操作会进 `dead_letter_operations` 而非无声悬挂(hermes 自己的 `delivery_obligations` 不在此列)

对执行计划的影响:

- postmortem **Option C(运维兜底)与 V4 正交,立即执行不等 merge**:daemon 定时重启 + 宿主侧 `attempting` 超时 watchdog
- **Option A/B 应在 merge 后立刻作为本地补丁实现**,而非在旧代码上做 — V4 的 WAL 调用点更多,旧分支上做了也要重写;建议优先 B(`NO_CKPT_ON_CLOSE` + close 前 passive checkpoint),侵入面小于 A
- 验证清单新增:合并后 hermes 嵌入宿主长稳运行观察(重点 `mslm-sync` 线程 close 路径)

### 5.6 作用域(three-scope)迁移安全性 — 实测确认 ✅

**功能来源(已核实,证据链完整):三层作用域是本项目设计实现并贡献给上游的功能。**

- **本地设计文档**:`docs/superpowers/plans/` 下 2026-04-25《multi-scope-memory》→ Phase2/2B/3 → 2026-04-26《scope-weights-and-entity-merge》→ 2026-05-15《scope-e2e-implementation》→ 2026-06-15《upstream-scope-pr-split》(PR 拆分计划:PR-A Schema/Migration → PR-B Retrieval → PR-C Interface)
- **上游落地提交**(2026-06-15,作者 kenyonxu,共 6 个):
  - `f9591e0f` schema 层:8 张表新增 `scope` + `shared_with` 列(即 PR-A,上游 #42)
  - `e9c49ddb` ScopeWeights 配置与持久化
  - `13c3445e` `_scope_where` 查询辅助 + 查询方法 scope 参数
  - `a270ff3a` scope 参数贯穿检索引擎与各通道
  - `5653d7e2` `recall_pipeline.run_recall()` scope 透传(即 PR-B,上游 #43)
  - `bf5e947b` MCP/CLI/Python API 暴露多作用域控制(即 PR-C,上游 #44)
- **上游维护者加固**(2026-06-18/19,Varun Pratap Bhardwaj,随 v3.6.15 发布):M016 迁移升级安全修复(`cac5eb0a`)、ScopeConfig 基础(`5d2ed9e1`)、shared 默认 OFF 的 opt-in 产品决策(`67a98d1c`)、DB 层 default-deny(`2be19f5e`)

因此分叉点 v3.6.22 两侧都有 scope——**那本来就是我们的代码**。V4 是在其基础上的进一步加固:准入网关鉴权 scope 写入、跨 scope 擦除覆盖全部表示层、V4 CHANGELOG 将 multi-scope 列为 V4 主打能力之一。

V4 对 scope 的影响逐项实测:

| 检查项 | 结果 |
|--------|------|
| `ScopeConfig` 默认值(shared 默认 OFF,opt-in) | 两版**逐字节一致**,无行为变化 |
| schema 中 scope 列(5 张表,`DEFAULT 'personal'`) | 完全一致;V4 第 7 处 "scope" 仅为 BM25 注释 |
| M018–M039 新迁移是否触碰 scope 列/语义 | **零** — M029/M031/M032 中的 "scope" 均指 profile 级索引/记账,与三层作用域无关 |
| Hermes 集成 | 显式传 `scope="personal"` / `include_global=True` / `include_shared=False`,对任何默认变化免疫 |

**结论:三层作用域的存量数据与语义 100% 完整迁移,无需任何适配。** 合并 V4 等于收回我们自己贡献的功能 + 上游两个版本周期(v3.6.15 → v4.0.1)的加固与事务化增强。mslm 品牌的 "Multi-Scope" 定位名正言顺——这是我们主导设计、实现并上游化的核心能力。

---

## 6. 合并策略:为什么选 merge 而非 rebase

| | merge upstream/main | rebase onto upstream/main |
|---|---|---|
| 冲突解决次数 | **1 次**(3 个文件) | 最多 19 次(逐提交重演,docs 提交反复碰 README/docs) |
| 历史 | 保留完整本地提交历史(已发布到 origin) | 重写已公开历史,force-push 风险 |
| 可回滚性 | 合并提交可整体 revert | rebase 后旧历史难以恢复 |
| 成本 | 低 | 高且收益不明显 |

**推荐执行序列**:

```bash
# 0. 数据备份(不可跳过)
cp -a ~/.superlocalmemory ~/.superlocalmemory.bak-v4-upgrade-$(date +%F)

# 1. 提交未完成的 hermes 1 行改动(fast=True 移除,与 V4 对齐)
git add src/superlocalmemory/integrations/hermes/__init__.py
git commit -m "fix(hermes): drop explicit fast=True to align with V4 client-driven-agentic default"

# 2. 合并
git checkout -b merge/upstream-v4.0.1
git merge upstream/main
# 解决 3 个冲突文件(策略见 §3.1),重点 review 3 个自动合并的核心文件(§3.2)

# 3. 验证(见 §7)
```

---

## 7. 合并后验证清单

### 静态验证
- [ ] `pyproject.toml`:name=mslm-memory、version=4.2.0、`torch>=2.11.0`、上游新依赖完整
- [ ] `config.py`:`for_mode()` 所有 `EmbeddingConfig(...)` 构造分支均保留 ollama 字段
- [ ] `embeddings.py`:worker env 块中 proxy 转发 + `HF_ENDPOINT` 移除存活
- [ ] `engine_wiring.py`:proxy 透传链路完整(config → init_embedder → EmbeddingService)
- [ ] `ruff check src/` 通过

### 测试
- [ ] `pytest tests/ -q --tb=short`(上游 ~2900 测试 + 本地 hermes 测试)
- [ ] 新增回归测试:config ollama 保留、embeddings proxy env、hermes include_global 默认行为

### 运行时(先在备份副本数据目录上跑,`SLM_DATA_DIR` 指向副本)
- [ ] `slm status` — 触发 M033–M039 迁移,确认无报错
- [ ] `slm doctor` — embedding worker(transformers 5.5.4 + 代理补丁)冷启动
- [ ] `slm remember "test"` + `slm recall "test"` — 验证准入网关下读写正常
- [ ] hermes-agent 实机:prefetch / sync_turn / on_memory_write / pre_compress 四路径
- [ ] `slm mcp` 与 MCP 客户端握手(SDK 2.0 无状态模式)

### 确认无误后
- [ ] 切换真实数据目录,观察 24h
- [ ] 保留备份至少一个版本周期

---

## 8. 后续上游贡献路线图(2026-08-13 立项)

### 8.1 已提交:PR #118 — WAL close-path 死锁修复

- **链接**:https://github.com/qualixar/superlocalmemory/pull/118
- **内容**:`DatabaseManager._connect()` 设 `SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE=1`(postmortem Option B)+ 行为级回归测试 + CHANGELOG
- **依据**:2026-08-13 生产事故(§5.5)+ 全量套件内复现(`test_search_profile_isolation_memory_counts` 无界挂起)
- **状态**:待上游 review。worktree 保留于 `/tmp/slm-pr-ckpt` 以备迭代
- **意义**:这是从真实事故反向定位的根因修复,附带生产/套件双重证据,是我们向上游建立贡献者信誉的关键 PR

### 8.2 候选:Hermes MemoryProvider 集成上游化

**现状核实(2026-08-13)**:上游 `src/superlocalmemory/integrations/` 目录**不存在**;Hermes 在上游仅以 MCP 客户端身份出现(`mcp/server.py` keepalive 注释、`tools_mesh.py` peer 身份、`docs/ide-setup.md` 配置指南)。我们的 MemoryProvider(prefetch/sync_turn/生命周期钩子/三工具)为 fork 独有。

**上游化障碍**:
1. 依赖 hermes-agent 侧 `MemoryProvider` 外部契约(prefetch/sync_turn/on_memory_write/on_pre_compress 签名),上游仓库无此契约定义,PR 需自带适配层说明
2. 9 个文件 + 测试,CI 需 mock hermes 侧契约
3. 上游产品叙事为 MCP-first;嵌入式 MemoryProvider 路线需维护者方向认可

**推进策略**:先等 PR #118 落地建立信誉 → 开 issue 探询维护者对 `integrations/` 目录的兴趣 → 若认可则按 multi-scope 的成功模式拆 3 个 PR(接口骨架 → provider 核心 → 工具三件套)。**不建议**未经探询直接提交大 PR。

### 8.3 候选:两个上游潜伏测试缺陷(小 PR)

merge 后测试修复中发现(见提交 `5ecfd733`):
1. `test_release_package_surface` 断言字面量 `python -m pytest tests/ -q`,但上游 `pypi-publish.yml` 实际使用 `RELEASE_TEST_ARGS` 数组展开——该测试在上游 CI 必然失败(或从未在发布分支上运行)
2. `test_rejects_python_less_than_3_11` 的 python3 shim 不覆盖带版本号的解释器名(python3.13 等),env 中存在多版本 python 时测试失真

两个都是 5 行内的小修复,适合作为 #118 之后的"热身跟进 PR"。

### 8.4 暂不上游化,保持 fork 独有

- mslm 品牌层(name/版本/README/文档)
- embeddings worker 代理转发 + `HF_ENDPOINT` 移除(中国大陆网络环境特定)
- `torch>=2.11.0` 宽松 pin(环境适配;上游有意硬 pin 以锁定 Apple Silicon 内存行为,上游化会被拒)
- `SLMConfig.proxy` 字段补全(若实现,可考虑上游化)

---

## 9. 嵌入式进程召回质量:daemon 路由(2026-08-13 实施)

### 9.1 根因(已查实)

嵌入 worker 是**有意的机器级单例**(2026-04-07 内存爆炸事故后,v3.4.13 引入 `.embedding-worker.pid` 守卫,全机仅 1 个 worker)。unified_daemon 持有它;gateway 内嵌的 `EmbeddingService` 检测到"被别的进程持有"后**自我禁用**(`embeddings.py:716-724`,`is_available=False` → `embed()` 返回 None)→ gateway 内 recall 的 semantic/hopfield/spreading_activation 三通道静默跳过,仅剩 BM25/entity/temporal/profile——**自 2026 年 4 月起知惠的召回质量一直缺三个通道**(postmortem 12:42:42 即有同样警告)。

对比:跨 encoder reranker 遇到同样单例时是**路由到 daemon 的 worker**;嵌入 service 只做了禁用、没做路由。代码注释自证设计意图:"Primary defense: daemon routing"(`embeddings.py:61`),CLI `observe` 已有 `daemon_request` 先例。

### 9.2 方案 A:provider daemon 路由(已实施 ✅)

provider 增加 daemon-first 路由层,daemon 不可达时回退进程内引擎:

- **recall**(`_engine_recall`):`GET /recall?q=&limit=&include_global=&include_shared=` → 守护进程的热嵌入 + reranker + 单例引擎全通道结果
- **store**(`_engine_store`):`POST /remember`(scope/shared_with 透传,speaker 进 metadata)→ daemon 的 canonical 写入路径(准入网关 + materializer),**顺带消除双进程 WAL 写竞争**(postmortem 拓扑)
- 7 个调用点全部重接(3 recall + 4 store)
- 顺修潜伏缺陷:`queue_prefetch` 缺线程重叠守卫
- 测试:hermes conftest 补 daemon 探测隔离(此前会打真实 daemon);新增 8 个路由测试(在线/离线/报错/非 ok × recall+store),**92/92 全绿**
- 联调实证:daemon 路由 recall 返回 score=0.647/conf=0.900 的全通道结果、**零 embedding-None 警告**;daemon 路由 store 落库成功

### 9.3 方案 B:EmbeddingService 通用路由(立项,上游候选)

在 slm 层修:`EmbeddingService` 检测到 worker 被他进程持有 **且** daemon 在线时,经 daemon 执行 embed(复用 reranker 的 WorkerPool IPC 或新增 daemon embed 端点)。受益面是所有嵌入消费方(MCP stdio server、任何嵌入式宿主),而非仅 hermes provider。

- 价值与 PR #118 同级,适合作为下一个上游贡献
- 前置:确认 daemon 侧 embed 能力端点形态(现有 `/recall` 是完整召回,不是纯 embed;WorkerPool IPC 是否可复用待调研)
- 预估:中工作量(daemon 端点 + EmbeddingService 路由分支 + 并发测试)

