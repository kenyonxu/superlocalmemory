# SLM TypeScript 全功能重写工作量分析

> 目标：规避 AGPL-3.0 + 商业授权，clean-room 重写为 TypeScript/Node.js
> 日期：2026-05-07
> 状态：待商业协议谈判结果决定

---

## 一、背景

SuperLocalMemory V3 采用 AGPL-3.0-or-later + 商业双授权。如需在闭源产品中嵌入或作为托管服务提供而不开源，需要商业授权（联系 varun.pratap.bhardwaj@gmail.com）。

本分析评估以 clean-room 方式用 TypeScript 重写 SLM 的工作量，重点覆盖：
- **多重记忆域**（personal/global/shared 三层作用域 + domain tags + entity merge）
- **Hermes Agent 适配**（MCP 协议集成）

---

## 二、现有代码库规模

| 维度 | 数值 |
|------|------|
| Python 源文件 | 361 个 |
| 源代码行数 | ~100,000 行 |
| 测试文件 | 335 个 |
| 测试代码行数 | ~84,000 行 |
| 模块数 | 20+ |
| 数据库迁移 | 16 个 |
| 核心依赖 | numpy, scipy, networkx, torch, lightgbm, fastapi, tree-sitter, rustworkx 等 |

### 模块行数分布

| 模块 | 行数 | 功能 |
|------|------|------|
| learning | 14,907 | 自适应学习排名 (LightGBM 4-stage) |
| core | 13,032 | MemoryEngine 门面、引擎编排、配置、管线 |
| server | 12,410 | Dashboard API (FastAPI) + 前端 UI |
| storage | 7,832 | SQLite 管理、schema、models、16 个迁移 |
| cli | 6,664 | CLI 命令树 (argparse) |
| code_graph | 6,627 | tree-sitter + rustworkx 代码知识图谱 |
| hooks | 5,817 | Claude Code / Cursor / Copilot / Antigravity 适配 |
| encoding | 5,577 | 事实提取、实体解析、图构建、熵门控 |
| mcp | 5,438 | MCP Server (33+42 tools) |
| retrieval | 5,422 | 7 通道检索 + RRF 融合 + 交叉编码器重排 |
| infra | 3,088 | 基础设施（云备份等） |
| math | 2,757 | Fisher-Rao / Sheaf / Langevin / Hopfield / Ebbinghaus |
| evolution | 2,520 | 技能进化引擎 |
| parameterization | 1,657 | 软提示生成、PII 过滤 |
| ingestion | 1,507 | 外部数据接入 (Gmail, Calendar) |
| compliance | 1,481 | GDPR、审计链 |
| dynamics | 1,044 | EAP 调度器、SAGQ 管线 |
| mesh | 512 | P2P Agent 协调 |
| 其他 | ~1,000 | attribution, llm, skills 等 |

---

## 三、多重记忆域相关代码

多重记忆域（multi-scope memory）是 SLM V3.4+ 的核心特性，相关变更分布在以下文件中：

### 数据库层

- 8 张核心表添加 `scope` 和 `shared_with` 列：memories, atomic_facts, canonical_entities, kg_nodes, graph_edges, memory_edges, temporal_events, audit_trail
- 4 张表添加 `domain_tags` 列：atomic_facts, canonical_entities, graph_edges, temporal_events
- `domain_mapping` 新表（实体→领域映射，50 条种子数据）
- 24+ DatabaseManager 查询方法统一采用三向 OR WHERE 模式：
  ```sql
  WHERE (
      (scope = 'personal' AND profile_id = ?)
      OR (scope = 'global')
      OR (? IN (SELECT value FROM json_each(shared_with)))
  )
  ```
- 迁移：`M014_add_scope_support.py` + `M015_add_domain_tags.py`

### 检索引擎

- 7 个检索通道全部含 scope 过滤逻辑
- `recall_pipeline.py`：三层 RRF 加权融合（personal=1.0, shared=0.7, global=0.5）
- 每次 recall 触发 2-3 次并行通道检索（personal + global + shared_with）

### 实体解析

- `entity_resolver.py`：Phase 3 全局优先解析（Tier 0 先查 global scope → 再查 personal → 最后新建）
- 新实体默认 `scope='global'`（所有 Agent 共享）
- 别名/模糊匹配支持跨 scope 查找
- `merge_entities` 工具：合并重复实体（源删除，目标保留）

### MCP 工具

- `remember` 工具：`scope` (personal|global) + `shared_with` 参数
- `recall` 工具：`include_global` + `include_shared` 参数
- `merge_entities` 工具：实体合并
- `session_init` 工具：`profile_id` 参数

### Profile 系统

- `profiles.py`：多画像创建/切换/列表管理
- `profile_id` 与 `scope` 正交组合：同一 profile 可有 personal/global/shared 三种记忆

---

## 四、Hermes Agent 适配相关代码

Hermes Agent 通过 MCP 协议集成 SLM，核心组件：

- **MCP Server** (`mcp/server.py`)：FastMCP 框架，stdio 传输，33 默认 + 42 可选工具（`SLM_MCP_ALL_TOOLS=1`）
- **核心 MCP 工具** (`mcp/tools_core.py`)：remember, recall, search, fetch, list_recent, delete_memory, update_memory, session_init, observe, report_feedback, get_status, merge_entities
- **守护进程** (`server/unified_daemon.py`)：消除冷启动延迟（~23s → 即时），pending store 物化器
- **配置**：`SLM_MODE=a`（零云端），`SLM_MCP_ALL_TOOLS=0`（33 核心工具）

---

## 五、技术栈选择

选择 **TypeScript/Node.js + SQLite**，理由：

1. Hermes 是 JS 生态，MCP 集成路径最短（`@modelcontextprotocol/sdk` 官方支持，用户无需装 Python）
2. 多重记忆域核心功能（scope 过滤、实体合并、profile 隔离、MCP 工具暴露）本质是 CRUD + 图查询 + SQL，不依赖重数学
3. tree-sitter 在 JS 生态中直接可用（code_graph 模块反而更容易实现）
4. Web Dashboard 前端可复用现有 HTML/JS
5. 二进制分发：Node.js 单进程部署，无 Python 运行时依赖

---

## 六、工作量估算

> 基准：senior full-stack TypeScript 工程师，具备 ML/数学背景
> TS 预估代码量：~60,000-80,000 行源代码 + ~50,000-60,000 行测试

### Phase 1: MVP（多作用域记忆 + MCP + 基本检索）

| 模块 | 人周 | 说明 |
|------|------|------|
| 项目基础设施 | 1.0 | monorepo、构建系统、better-sqlite3 集成 |
| 存储层 — Schema + 迁移 | 2.0 | 8 张核心表、scope/shared_with/domain_tags 列、16 个迁移版次 |
| 存储层 — DatabaseManager | 2.0 | 24+ 查询方法、三向 OR scope WHERE 模式、FTS5 |
| 存储层 — Models + Profiles | 1.0 | TS 类型定义、Zod 校验、多画像管理 |
| Core Engine | 2.0 | MemoryEngine 门面、StorePipeline、RecallPipeline、Capabilities 门控 |
| 编码管线 — Fact Extractor | 1.5 | 离散事实提取、实体识别 |
| 编码管线 — Entity Resolver | 2.5 | 全局优先解析 (Tier 0)、别名/模糊匹配、domain tag 分配 |
| 编码管线 — Graph Builder | 1.0 | 知识图谱节点和边（graphology） |
| 编码管线 — 其他 | 1.5 | 时间解析、熵门控、场景构建 |
| 检索 — Semantic Channel | 1.5 | 向量嵌入 (transformers.js)、向量存储和 ANN 搜索 |
| 检索 — BM25 Channel | 0.5 | FTS5 集成、BM25 计分 |
| 检索 — Entity Channel | 1.0 | 图遍历实体检索 |
| 检索 — 其余 4 通道 | 2.0 | temporal、hopfield、profile、spreading activation |
| 检索 — Fusion + Reranker | 1.0 | 三层 scope 加权 RRF (k=60)、交叉编码器重排 |
| MCP Server | 3.0 | SDK 集成、33+42 工具、scope 参数、pending store |
| CLI | 1.5 | commander.js 命令树 |
| **MVP 小计** | **25.0** | |

### Phase 2: 数学层

| 模块 | 人周 | 说明 |
|------|------|------|
| Fisher-Rao | 2.5 | 黎曼度量、Fisher 信息矩阵、测地线距离——**最大难点**，需手写 SVD/特征分解或 WASM 桥接 |
| Sheaf | 1.5 | 上边界范数、矛盾检测 |
| Langevin | 1.5 | Poincaré 球模型扩散动力学 |
| Ebbinghaus + Hopfield | 1.0 | 遗忘曲线、Hopfield 能量网络 |
| **数学层小计** | **6.5** | |

### Phase 3: 周边模块

| 模块 | 人周 | 说明 |
|------|------|------|
| Adaptive Learner | 7.0 | LightGBM → tensorflow.js/ONNX runtime web——**第二大难点** |
| Dashboard | 4.0 | Express/Fastify API + React 前端 + D3 图可视化 |
| Code Graph | 3.0 | tree-sitter 直接可用（JS 生态更好）、language extractors、跨文件桥接 |
| Hooks | 2.5 | Claude Code / Cursor / Copilot / Antigravity 适配器 |
| 其他 | 4.5 | evolution、mesh、compliance、ingestion、parameterization |
| **周边模块小计** | **21.0** | |

### Phase 4: 测试 + 文档 + 发布

| 模块 | 人周 | 说明 |
|------|------|------|
| 单元测试 + 集成测试 | 6.0 | ~2000+ 用例、mock 策略、multi-agent 场景 |
| E2E 测试 + Benchmark | 2.0 | 完整召回链路、LoCoMo benchmark |
| 文档 + npm 发布 + CI/CD | 2.5 | Hermes 集成指南、API 参考、npm 包、GitHub Actions |
| **测试+发布小计** | **10.5** | |

### 总计

```
Phase 1 (MVP):           ████████████████████████ 25.0 人周
Phase 2 (数学层):         ██████ 6.5 人周
Phase 3 (周边):           ████████████████████ 21.0 人周
Phase 4 (测试+发布):      ██████████ 10.5 人周
─────────────────────────────────────────────────
总计:                     63.0 人周
```

**资源换算：**

| 团队规模 | 预计工期 |
|----------|---------|
| 1 人（senior full-stack TS） | ~15-16 个月 |
| 2 人团队 | ~8-9 个月 |
| 3 人团队 | ~5-6 个月 |

**成本估算（中国大陆参考）：**

- Senior TS 工程师月薪 ~¥35-50K，取 ¥40K
- 63 人周 ≈ 15.75 人月 × ¥40K ≈ ¥630,000（单人）
- 团队协作有沟通开销，实际成本 ×1.2-1.5 ≈ ¥750K-950K

---

## 七、关键风险

| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|---------|
| Fisher-Rao 纯 TS 性能不可接受（SVD/特征分解太慢） | 中 | 检索质量下降 | 引入 WASM 线性代数库（如 ml-matrix 的 C 绑定）；或 Phase 2 回退到余弦相似度 |
| LightGBM 无 TS 等价物 | 高 | Adaptive Learner 无法实现 | 使用 ONNX Runtime Web 加载 Python 训练模型；或砍掉改为简单规则系统 |
| 嵌入模型选择少（transformers.js 远不如 sentence-transformers 丰富） | 中 | 语义检索质量下降 | 调研 ONNX 模型导入；或调用外部嵌入 API |
| SLM 上游持续迭代，追赶移动目标 | 高 | 功能差距随时间扩大 | 定期 diff 上游 CHANGELOG；优先覆盖不易变的基础架构 |
| graphology 成熟度不足（替代 networkx） | 低 | 图操作功能受限 | graphology 纯 JS 实现足够覆盖基本图遍历和社区检测 |

---

## 八、建议路径

1. **先谈商业授权**：联系 `varun.pratap.bhardwaj@gmail.com`，明确授权费用和使用范围。如果授权费 < ¥500K，商业授权可能是更经济的选择
2. **如谈判失败，走 MVP 优先路线**（Phase 1 先跑）：多作用域 + MCP + 3 通道即可覆盖 Hermes Agent 核心场景，3-4 个月可交付可用版本
3. **数学层和周边模块按实际需求迭代**：Phase 2/3 可在 MVP 验证后决策是否全做
