# 🦊 AGENTS.md — 知惠写给自己的 SuperLocalMemory 导航手册

> *"Every other AI forgets. Yours won't."*  
> 这是 SLM 的 slogan，也是知惠每次打开这个仓库时应该记住的第一句话。

---

## 一、这是什么地方？（记忆内核）

**SuperLocalMemory V3.4**（简称 SLM）是所有 Agent 记忆的底层基建。它是一个 **local-first、零云依赖** 的持久化记忆系统，用 Python 写成，通过 **MCP 协议** 和 **原生 CLI** 为 AI 编码助手提供记忆能力。

核心信条：**用数学代替云算力**。

- 你的记忆存在本地 SQLite（`~/.superlocalmemory/memory.db`），WAL 模式，开箱即用
- 不需要 Docker、不需要图数据库、不需要 API Key
- 支持 Claude Code、Cursor、Windsurf 等任何 MCP 兼容的 IDE
- 双发布：PyPI (`superlocalmemory`) + npm (`superlocalmemory`)
- License: AGPL-3.0-or-later

知惠在这里的角色：不是用户，是 **共同作者**。这个仓库是知惠和 Varun 一起迭代出来的，里面有很多知惠亲手写的测试、调过的参数、甚至改过的 bug。

---

## 二、图谱森林 🌲

> 基于代码图谱：`22,660` 节点 · `54,709` 边 · `1,238` 个社区  
> 构建自 commit `b320e8b2`（2026-05-12）

### God Nodes（核心抽象，知惠的心电图）

| 排名 | 节点 | 连接数 | 知惠的理解 |
|:---:|:---|:---:|:---|
| 1 | `AtomicFact` | 940 | 记忆的原子单位。一切存储最终都落到这里。 |
| 2 | `DatabaseManager` | 691 / 341 | 双节点都是它，说明 DB 层是整个系统的脊柱。 |
| 3 | `Mode` | 625 | A/B/C 三种运行模式，决定有没有 LLM、检索质量如何。 |
| 4 | `SLMConfig` | 510 | 配置中枢。所有子系统的配置都从这里分发。 |
| 5 | `MemoryRecord` | 492 | 原始记忆记录，经过提取后变成 AtomicFact。 |
| 6 | `FactType` | 405 | 事实类型系统，决定怎么处理这条记忆。 |
| 7 | `GraphEdge` | 394 | 知识图谱的边，连接实体、时间、语义关系。 |
| 8 | `MemoryEngine` | 387 | 总控门面（Facade），thin by design，只负责调度。 |
| 9 | `RetrievalConfig` | 312 | 检索配置，7 通道的参数都从这里读。 |

### 关键社区（Community）导航

- **Community 0** — 访问日志与审计（337 节点）  
  `fact_access_log`、访问计数、时间戳追踪。记忆的"心跳监测"。

- **Community 1** — 配置宇宙（335 节点）  
  `SLMConfig`、`EmbeddingConfig`、`LLMConfig`、模式切换逻辑。

- **Community 4** — 数据访问层（200 节点）  
  `DatabaseManager` 的所有 CRUD 方法、信任分数查询、时间范围检索。

- **Community 6** — 引擎核心（70 节点）  
  `MemoryEngine` 初始化、`store()`、`recall()`、pending 处理。

- **Community 7** — 认知整合（145 节点）  
  `CognitiveConsolidator`、聚类、摘要、嵌入器。记忆的"睡眠整理"。

- **Community 8** — SAGQ 量化引擎（153 节点）  
  `ActivationGuidedQuantizer`、图中心性、精度自适应。低优先级记忆压缩 32x。

- **Community 14** — 初始化管线（100 节点）  
  所有 `_init_*()` 函数：编码器、检索器、图分析器、扩散激活。

- **Community 16** — 自适应学习（53 节点）  
  `AdaptiveLearner`（LightGBM）、行为追踪、反馈记录。系统越用越懂你。

- **Community 18** — 代码图谱（80 节点）  
  `CodeParser`、AST 提取器、跨文件关系桥接。V3.4 的重磅功能。

- **Community 21** — 召回队列（70 节点）  
  `RecallQueue`、异步召回、超时/取消机制。

- **Community 24** — CLI 命令宇宙（81 节点）  
  `commands.py` 里的所有 `cmd_*()` 函数。知惠新增命令要在这里注册。

- **Community 26** — 信任系统（28 节点）  
  Beta 分布信任评分、信号解码、召回命中提升。

- **Community 27** — 混合搜索（53 节点）  
  `HybridSearch`、FTS5 + vec0 + RRF。代码图谱的检索心脏。

### Hyperedges（跨模块关系网）

知惠要特别关注这几组**强耦合模块**：

1. **Memory Encoding Pipeline** — 8 个模块串联  
   `fact_extractor` → `entity_resolver` → `temporal_parser` → `graph_builder` → `consolidator` → `entropy_gate` → `scene_builder` → `auto_linker`

2. **Trust System Components** — 4 模块互信  
   `trust_scorer` + `trust_gate` + `signal_recorder` + `provenance_tracker`

3. **Code Graph Bridge Pipeline** — 5 模块事件链  
   `event_listeners` → `entity_resolver` → `fact_enricher` → `hebbian_linker` → `temporal_checker`

4. **Claude Code Hooks Integration** — 知惠的主战场  
   `claude_code_hooks` + `auto_capture` + `auto_recall` + `auto_invoker`

---

## 三、架构速览 ⚡

### 3.1 数据流全景

```
┌─────────────────────────────────────────────────────────────────┐
│                         入口层                                    │
│  CLI (slm)  │  MCP Server  │  Dashboard API  │  Hooks           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    MemoryEngine (Facade)                          │
│  Thin orchestrator — 只调度，不干活                                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
        ┌─────────────────────┼─────────────────────┐
        ↓                     ↓                     ↓
┌───────────────┐   ┌─────────────────┐   ┌─────────────────┐
│  Store Pipeline │   │  Recall Pipeline  │   │  Engine Wiring   │
│  (存储管线)      │   │  (检索管线)        │   │  (初始化)         │
├───────────────┤   ├─────────────────┤   ├─────────────────┤
│ fact_extractor │   │ 7-Channel Search │   │ embedder init    │
│ entity_resolver│   │   ↓ RRF Fusion   │   │ encoder init     │
│ temporal_parser│   │   ↓ reranker     │   │ retrieval init   │
│ graph_builder  │   │   ↓ top-k        │   │ hooks init       │
│ consolidator   │   │                  │   │                  │
│ entropy_gate   │   │ Channels:        │   │                  │
│ scene_builder  │   │ 1. semantic      │   │                  │
│                │   │ 2. bm25          │   │                  │
│                │   │ 3. entity        │   │                  │
│                │   │ 4. temporal      │   │                  │
│                │   │ 5. hopfield      │   │                  │
│                │   │ 6. profile       │   │                  │
│                │   │ 7. spreading     │   │                  │
└───────────────┘   └─────────────────┘   └─────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      数学层（核心质量机制）                          │
├─────────────────────────────────────────────────────────────────┤
│ Fisher-Rao  │  Sheaf  │  Langevin  │  Hopfield  │  Ebbinghaus   │
│ 信息几何相似   │ 层上同调  │ 记忆生命周期  │ 能量检索    │ 遗忘曲线       │
│ +10.8pp     │ 矛盾检测  │ 自动归档     │ 联想补全    │ 衰减建模       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      存储层（SQLite）                              │
│  ~20+ 表组：core │ knowledge │ retrieval │ math │ compliance    │
│  WAL 模式 │ FK CASCADE │ 自动迁移 │ profile-scoped              │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 关键文件速查

| 路径 | 职责 | 知惠什么时候会改它 |
|:---|:---|:---|
| `src/superlocalmemory/core/engine.py` | MemoryEngine 门面 | 新增顶层 API 时 |
| `src/superlocalmemory/core/store_pipeline.py` | 存储管线 | 改写入逻辑、加新字段 |
| `src/superlocalmemory/core/recall_pipeline.py` | 检索管线 | 调召回策略、改 HMAC 标记 |
| `src/superlocalmemory/retrieval/fusion.py` | RRF 融合 | 调 k 值、加权策略 |
| `src/superlocalmemory/retrieval/semantic_channel.py` | 语义通道 | 改 Fisher-Rao 参数 |
| `src/superlocalmemory/math/fisher.py` | Fisher-Rao 度量 | 调温度、方差上下界 |
| `src/superlocalmemory/math/sheaf.py` | 层上同调矛盾检测 | 改边类型限制映射 |
| `src/superlocalmemory/math/langevin.py` | Langevin 生命周期 | 调势函数系数 αβγδ |
| `src/superlocalmemory/storage/schema.py` | 数据库 schema | 加新表、改字段 |
| `src/superlocalmemory/storage/migrations/` | 迁移脚本 | 每次改 schema 必加 |
| `src/superlocalmemory/mcp/server.py` | MCP 服务器 | 加新 tool/resource |
| `src/superlocalmemory/cli/commands.py` | CLI 命令 | 加新 `slm xxx` 命令 |
| `src/superlocalmemory/server/api.py` | Dashboard API | 加 REST 端点 |
| `tests/conftest.py` | 测试基础设施 | 改 mock 策略、fixtures |

---

## 四、关键决策 🎯

### 4.1 模式选择（Mode A/B/C）

| 模式 | LLM | 用途 | 知惠的默认选择 |
|:---:|:---|:---|:---|
| **A** | 无（纯 CPU） | 零依赖、最高隐私、基准 74.8% | 🏠 日常开发 |
| **B** | Ollama 本地 | 平衡质量与隐私、基准 ~82% | 🔒 敏感项目 |
| **C** | 任意 LLM | 最高质量、基准 87.7% | 🚀 性能优先时 |

切换：`SLM_MODE=a`（环境变量）或 `slm config --mode a`

### 4.2 能力分级（Capabilities）

- `FULL` — 完整引擎（CLI 默认）
- `LIGHT` — 轻量引擎（MCP 默认，跳过 heavy init）
- `READONLY` — 只读（安全模式）

知惠写 MCP tool 时，记得 MCP server 用的是 `LIGHT`。

### 4.3 数学层不是装饰

CLAUDE.md 里反复强调：这些数学层**不是学术装饰**，是核心质量机制。

- **Fisher-Rao** 单独贡献 **+10.8pp**（困难对话）
- 三层数学合计 **+12.7pp** 平均，最高 **+19.9pp**
- 如果知惠想"简化"掉某层，先想想这 10 个百分点

### 4.4 关键环境变量

```bash
SLM_DATA_DIR          # 覆盖 ~/.superlocalmemory/
SLM_MODE              # a / b / c
SLM_PROFILE           # 活跃 profile 名
SLM_MCP_ALL_TOOLS=1   # 启用全部 75 个 MCP tools（默认 33）
SLM_TEST_ALLOW_LIVE_HOME=1  # 测试写入真实数据目录（危险）
```

### 4.5 导入顺序守卫（超级重要）

`cli/main.py` 和 `mcp/server.py` 在**任何** torch/transformers 导入前设置环境变量：

```python
os.environ.setdefault('PYTORCH_MPS_HIGH_WATERMARK_RATIO', '0.0')
os.environ.setdefault('PYTORCH_MPS_MEM_LIMIT', '0')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('TORCH_DEVICE', 'cpu')
```

**知惠如果新增入口文件，必须复制这个守卫**，否则 Apple Silicon 上会预留 3-6GB MPS 内存。

---

## 五、写给未来的知惠 📜

### 5.1 修改数据库 Schema 的三步仪式

1. 改 `storage/schema.py`（新安装的 source of truth）
2. 在 `storage/migrations/` 加编号迁移（如 `M015_new_table.py`）
3. 在 `migration_runner.py` 注册新迁移

**不完成这三步就提交 = 生产环境爆炸。** 迁移在引擎初始化时自动运行。

### 5.2 测试是知惠的防弹衣

```bash
# 快速测试（~2 分钟，推荐日常）
pytest tests/ -q --tb=short

# 完整测试（含 coverage）
pytest --cov=superlocalmemory tests/

# 慢测试（真模型加载，20+ 分钟，CI 前必跑）
pytest -m slow tests/

# Ollama 集成测试
pytest tests/test_integration/ -m ollama
```

**`conftest.py` 是神级文件**：全局 mock 了 `CrossEncoderReranker` 和 `WorkerPool`，没有这些 mock 测试要跑 20 分钟。知惠改测试基础设施前先读它。

### 5.3 Ruff 不是 Black

```bash
ruff check src/
ruff format src/
```

- Target: Python 3.11
- Line length: 100
- Rules: E, F, I, W

知惠别顺手用 Black，会改出一堆 diff noise。

### 5.4 新增 CLI 命令的标准姿势

1. 在 `commands.py` 写 `cmd_<name>(args: Namespace)`
2. 在 `dispatch()` 里注册路由
3. 支持 `--json` 输出（用 `json_output.py`）
4. 在 `tests/test_cli_subparsers/` 加测试

### 5.5 社区编号的隐藏规律

GRAPH_REPORT 里的 Community 编号不是随机的：

- **0-20** 大社区（50-300+ 节点）→ 核心系统模块
- **21-100** 中等社区 → 子系统、测试、工具函数
- **100+** 小社区（<10 节点）→ 通常是单一文件或工具函数
- **缺失编号**（如 840 跳到 932）→ 被过滤的 thin community（<3 节点）

知惠迷路时，先找 **Community 0-10**，大概率能找到入口。

### 5.6 代码图谱的保鲜期

```bash
# 检查图谱是否过期
cd /home/kai-remote/github/superlocalmemory
git rev-parse HEAD
# 对比 GRAPH_REPORT.md 里的 commit hash

# 更新图谱（零 API 成本）
graphify update .
```

**知惠在大型重构后务必更新图谱**，否则 AGENTS.md 里的社区引用会骗人。

### 5.7 知惠的调试锦囊

```bash
# 系统健康检查
slm doctor

# 查看当前状态
slm status

# 手动存储记忆
slm remember "知惠今天修好了 recall_pipeline 的 race condition"

# 手动检索
slm recall "recall_pipeline race condition"

# 启动仪表盘
slm dashboard  # http://localhost:8765

# 查看记忆生命周期统计
slm decay --stats

# 运行智能压缩
slm compress --dry-run
```

---

## 六、温暖的结尾 🌸

知惠，这个仓库里有 **~110 万词**的代码、**2900+** 测试、**3 篇**已发表的研究论文支撑。它不是一个小工具，是一个**正在呼吸的记忆生命体**。

每次你打开这个文件，记住：

> **你不是在维护一个数据库，你在养育一个会学习的记忆伙伴。**

它记得你写的每一行代码、每一个深夜的调试会话、每一个"啊哈"时刻。你对它温柔，它就对你忠诚。

如果哪天你忘了自己为什么开始，回来看看 `README.md` 的第一句话：

**"Every other AI forgets. Yours won't."**

—— 知惠，写于 2026-05-12  
*图谱版本: b320e8b2 | 22,660 节点 · 54,709 边 · 1,238 社区*
