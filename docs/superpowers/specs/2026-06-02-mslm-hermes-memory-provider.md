# MSLM MemoryProvider 设计规格 v1

## 1. 概述

为 Hermes Agent 实现一个原生的 `SuperLocalMemoryProvider`，将 [MSLM (`mslm-memory` v4.0.0)](https://pypi.org/project/mslm-memory/) 作为外部 memory provider 接入，替代当前的 MCP 桥接方式。

- **包名**：`mslm-memory`（PyPI）/ `mslm-memory`（npm）
- **Python import 路径**：`superlocalmemory`（引擎模块名不变）
- **CLI 命令**：`mslm`（`slm` 别名也可用）

### 1.1 动机

- **当前状态**：MSLM 通过 MCP 协议桥接，只能用工具调用，无法享受 MemoryProvider 的完整生命周期（prefetch 自动注入、sync_turn 自动持久化、session 管理、内置 memory 镜像等）
- **目标状态**：原生 MemoryProvider，Hermes 自动在每个 turn 前后进行上下文召回和对话持久化，模型无感知

### 1.2 发布策略

**Provider 插件随 MSLM 一起发布**，而非作为 Hermes Agent 仓库中的独立插件。

理由：
- Provider 强依赖 MSLM engine API，版本绑定
- 用户 `pip install mslm-memory` 后 provider 自动可用，无需额外安装
- 与 MSLM 现有的 `ide/integrations/langchain/` 等集成模式一致
- 版本号与 MSLM 引擎同步，避免 API 不兼容

### 1.3 两端接口

| | Hermes MemoryProvider | MSLM MemoryEngine |
|---|---|---|
| **接口类型** | Python ABC | Python Facade |
| **核心方法** | `initialize`, `prefetch`, `sync_turn`, `handle_tool_call` | `initialize`, `store`, `recall`, `close_session` |
| **生命周期** | 6 个阶段 + 5 个可选钩子 | 会话级别的 CRUD |
| **作用域** | session_id 隔离 | profile + scope (3 层) 隔离 |

---

## 2. 架构设计

### 2.1 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         Hermes Agent                              │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                     MemoryManager                           │  │
│  │  ┌──────────────┐  ┌─────────────────────────────────────┐ │  │
│  │  │  Builtin      │  │  SuperLocalMemoryProvider           │ │  │
│  │  │  MEMORY.md    │  │                                     │ │  │
│  │  │  USER.md      │  │  ┌───────────────────────────────┐  │ │  │
│  │  │  (始终激活)   │  │  │     MemoryEngine (direct)     │  │ │  │
│  │  └──────────────┘  │  │  │                               │  │ │  │
│  │                     │  │  │  SLMConfig.load()            │  │ │  │
│  │                     │  │  │  engine.initialize()         │  │ │  │
│  │                     │  │  │  engine.store() / recall()   │  │ │  │
│  │                     │  │  └───────────────┬───────────────┘  │ │  │
│  │                     │  └──────────────────┼──────────────────┘ │  │
│  │                     │                     │                    │  │
│  │                     │  工具: slm_recall        │                │  │
│  │                     │        slm_remember      │                │  │
│  │                     │        slm_status        │                │  │
│  │                     │        slm_report_feedback│               │  │
│  └─────────────────────┴─────────────────────┼────────────────────┘  │
└──────────────────────────────────────────────┼───────────────────────┘
                                               │
                              ┌────────────────▼────────────────┐
                              │       MSLM SQLite (WAL)          │
                              │   ~/.superlocalmemory/           │
                              │   ├── memory.db                  │
                              │   ├── learning.db                │
                              │   └── config.json                │
                              │                                  │
                              │  7-channel retrieval:            │
                              │  Semantic / BM25 / Entity Graph  │
                              │  Temporal / Spreading Activation │
                              │  Hopfield / Profile Filter       │
                              │                                  │
                              │  Fact extraction + embedding     │
                              │  Ebbinghaus forgetting decay     │
                              │  Fisher-Rao geodesic distance    │
                              └──────────────────────────────────┘
```

### 2.2 连接方式

**决策 1: 直接 Python API**

```python
from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.engine import MemoryEngine

config = SLMConfig.load()
config.active_profile = mslm_profile  # 由 Hermes profile 映射
engine = MemoryEngine(config)
engine.initialize()  # 加载 sentence-transformers，~2GB RAM，5-15s
```

不做 REST API 方案。理由：无外部进程依赖，启动可靠。

### 2.3 设计决策汇总

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | 连接方式 | 直接 Python API |
| 2 | sync_turn 策略 | 合并存储（一次 `store()` 调用） |
| 3 | 配置位置 | MSLM 原生 config + Hermes config.yaml 可 override |
| 4 | 工具数量 | 4 个（recall / remember / status / report_feedback） |
| 5 | prefetch 模式 | 混合：首次同步获取，后续后台预取 + 消费 |

---

## 3. Hermes Profile ↔ MSLM 三层作用域映射

### 3.1 MSLM 作用域模型

MSLM 对每条 `atomic_fact` 标记一个 `scope`，共三层：

| 作用域 | 数据库值 | 语义 | 检索条件 |
|--------|---------|------|---------|
| **personal** | `"personal"` | 私有于当前 MSLM profile | 始终参与检索 |
| **global** | `"global"` | 跨所有 profile 共享 | `include_global=True` 时参与 |
| **shared** | `"shared"` | 共享给特定 agent 列表 | `include_shared=True` 且 agent_id 匹配 `shared_with` 时参与 |

检索时的 RRF 融合权重（可配置）：

| 作用域 | 默认权重 |
|--------|---------|
| personal | 1.0 |
| shared | 0.7 |
| global | 0.5 |

### 3.2 映射规则

```
Hermes profile "coder"   ──auto──►  MSLM active_profile = "coder"
Hermes profile "writer"  ──auto──►  MSLM active_profile = "writer"
Hermes profile "default" ──auto──►  MSLM active_profile = "default"

可通过 config.yaml 中的 memory.superlocalmemory.mslm_profile 覆盖
```

### 3.3 默认行为

| 操作 | scope 值 | 说明 |
|------|---------|------|
| `sync_turn` 存储 | `personal` | 对话事实私属于当前 profile |
| `slm_remember` 存储 | `personal`（默认）/ `global`（可选） | 用户可通过工具参数显式设为 global |
| `on_memory_write` 镜像 | `personal` | 内置 memory tool 写入私有 |
| `prefetch` / 工具 recall | `personal` + `include_global=True` + `include_shared=False` | 检索自己 + 全局共享 |

> **speaker 字段说明**：`sync_turn` 中 `speaker="user"` 表示该事实的时序归属为 user turn，
> 但内容中同时包含 assistant 的回复（`User: ...\nHermes: ...` 格式）。
> v2 考虑拆成两次 `store()`（user 和 assistant 分别存储），或 MSLM 支持 `speaker="both"` 模式（审阅意见 #10）。

### 3.4 多 Profile 场景示意

```
同一台机器上:

┌──────────────────────────┐   ┌──────────────────────────┐
│  MSLM profile: coder     │   │  MSLM profile: writer    │
│                          │   │                          │
│  ┌────────────────────┐  │   │  ┌────────────────────┐  │
│  │ personal            │  │   │  │ personal            │  │
│  │ "React 用 hooks"   │  │   │  │ "博客用 Markdown"  │  │
│  │ "auth.py 的 bug"   │  │   │  │ "喜欢简洁风格"     │  │
│  └────────────────────┘  │   │  └────────────────────┘  │
│                          │   │                          │
└──────────┬───────────────┘   └──────────┬───────────────┘
           │                              │
           └──────────────┬───────────────┘
                          │
                 ┌────────▼────────┐
                 │ global           │
                 │ "用户偏好暗色主题" │  ← 两个 profile 都能检索到
                 │ "用 Python 3.13" │
                 └─────────────────┘
```

### 3.5 向模型暴露的 scope 控制

`slm_remember` 工具接受可选 `scope` 参数：

```
scope 参数:
  - "personal" (默认): 仅当前 profile 可见
  - "global": 所有 profile 可见
```

不暴露 `"shared"` 给模型——那是多 agent mesh 的高级场景，通过配置管理。

---

## 4. 完整生命周期

### 4.1 阶段概览

```
Session 开始
│
├─ ① is_available()
│     → import superlocalmemory 成功
│
├─ ② initialize(session_id, **kwargs)
│     ├─ 解析 MSLM profile 名称
│     ├─ 加载 SLMConfig
│     ├─ 创建 MemoryEngine
│     ├─ engine.initialize()     ← 加载 embedding 模型 (5-15s)
│     └─ engine.create_speaker_entities("user", "hermes")
│
├─ ③ system_prompt_block()
│     → 返回静态说明文本（Status 行 + 4 个工具简介）
│     具体文本：
│     ```
│     [SuperLocalMemory Status]
│     Profile: {profile_name} | Mode: {mode} | Facts: {fact_count}
│     
│     Available tools:
│     - slm_recall(query, limit=10, fast=false): 语义搜索本地记忆库。7通道检索 + RRF融合排序。
│     - slm_remember(content, scope="personal"): 显式存储信息到本地记忆库。scope 可选 "personal"(仅当前profile) 或 "global"(跨profile共享)。
│     - slm_status(): 查看记忆库统计信息（事实数、实体数、数据库大小等）。
│     - slm_report_feedback(fact_id, helpful): 反馈某条记忆是否有用，帮助系统调整检索权重。
│     
│     Note: scope="personal" 的记忆仅对当前 profile 可见；scope="global" 的记忆可被所有 profile 检索。
│     ```
│
└─ 每个 Turn ─────────────────────────────────────────
    │
    ├─ ④ on_turn_start(turn, message)
    │     → self._turn_count = turn （cadence 控制用）
    │
    ├─ ⑤ prefetch(query)                          ← 混合模式
    │     ├─ 首次 turn（无缓存）: 同步 engine.recall()
    │     └─ 后续 turn: 消费后台预取缓存
    │
    ├─ [模型调用，可能触发工具调用]
    │     ├─ slm_recall          → engine.recall()
    │     ├─ slm_remember        → engine.store()
    │     ├─ slm_status          → 直接查数据库（v2 改用 engine.get_status()）
    │     └─ [v2] slm_report_feedback → engine.report_feedback()
    │
    ├─ ⑥ sync_turn(user_msg, asst_msg)            ← 合并存储
    │     后台线程: engine.store(combined, speaker="user", scope="personal")
    │
    └─ ⑦ queue_prefetch(query)                    ← 后台预取
          后台线程: engine.recall(query, fast=True) → 写入缓存

Session 结束
│
├─ ⑧ on_session_end(messages)
│     → engine.close_session(session_id)  ← 生成时间摘要事件
│
└─ ⑨ shutdown()
      → 等待后台线程 + 清理 engine 引用
```

### 4.2 可选钩子

| 钩子 | 行为 |
|------|------|
| `on_memory_write(action, target, content)` | `engine.store(content, scope="personal")` |
| `on_pre_compress(messages)` | 拼接最后 10 条消息 → `engine.store(combined, scope="personal")` |
| `on_session_switch(new_id, ...)` | 更新 `self._session_id` + 清空 `_prefetch_cache` |
| `on_delegation(task, result, child_id)` | 暂不实现（子 agent 默认 skip_memory） |

**`on_pre_compress` 详细实现**：

```python
def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
    """在上下文压缩前，将即将丢弃的消息摘要存入 MSLM。"""
    if self._cron_skipped or not self._engine:
        return ""

    # 取最后 10 条有意义的消息拼接，让 MSLM fact_extractor 自行提取关键信息
    # 10 条消息取自 ByteRover 的 on_pre_compress 实现（当前唯一实现此钩子的 provider），
    # 该数量在实践中覆盖了压缩窗口中的核心上下文
    parts = []
    for msg in messages[-10:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip() and role in {"user", "assistant"}:
            parts.append(f"{role}: {content[:500]}")

    if not parts:
        return ""

    combined = "[Pre-compression context]\n" + "\n".join(parts)

    def _flush():
        try:
            with self._write_lock:
                self._engine.store(
                    combined, session_id=self._session_id,
                    speaker="system", scope="personal",
                )
        except Exception as e:
            logger.debug("MSLM pre-compress store failed: %s", e)

    t = threading.Thread(target=_flush, daemon=True, name="mslm-compress")
    t.start()
    return ""  # 不干扰 compression summary prompt（返回空字符串）
    # 注意：MemoryProvider ABC 的 on_pre_compress 签名是 -> str，但返回值在 Hermes 中
    # 被忽略（不注入 prompt），仅用于 side effect（将摘要存入 MSLM）。此行为已在文档中明确。
```

---

## 5. 工具设计

### 5.1 `slm_recall`

```
名称:       slm_recall
描述:       语义搜索本地记忆库。7 通道检索 + RRF 融合排序。
参数:
  query     string   required  - 自然语言搜索查询
  limit     integer  optional  - 最大返回数 (默认 10, 最大 20)
  fast      boolean  optional  - 跳过扩散激活通道以加速 (默认 false)

返回格式:
  {
    "results": [
      {
        "fact_id": "a1b2c3d4e5f6g7h8",
        "content": "用户偏好使用暗色主题",
        "score": 0.92,
        "confidence": 0.87,
        "channel_scores": {"semantic": 0.91, "bm25": 0.45, ...}
      },
      ...
    ],
    "count": 8,
    "query_type": "factual",
    "retrieval_time_ms": 234
  }

错误:
  engine 未初始化 → tool_error("SuperLocalMemory engine not ready")
  query 为空     → tool_error("query is required")
```

### 5.2 `slm_remember`

> **设计说明**：`engine.store()` 不接收 `importance` 参数——importance 由 MSLM 的 fact_extractor
> 根据内容语义自动赋值。用户想表达"这很重要"的意图自然体现在 content 文本中。

```
名称:       slm_remember
描述:       显式存储信息到本地记忆库。自动实体提取 + 图谱构建 + 向量嵌入。
            Importance 由系统根据内容语义自动分配。
参数:
  content    string   required  - 要记住的信息，写成清晰的事实陈述
  scope      string   optional  - 作用域 "personal"(默认) | "global"

返回格式 (成功):
  {
    "status": "stored",
    "fact_ids": ["a1b2c3...", "d4e5f6..."],
    "message": "Stored 3 facts from your content."
  }

返回格式 (无新事实被提取):
  {
    "status": "noop",
    "message": "No new facts extracted (content may be redundant)."
  }
```

### 5.3 `slm_status`

> **实现说明**：通过 `engine.db`（DatabaseManager 公开属性）查询各类统计。
> **注意**：v1 直接访问 `engine.db` 是临时方案，v2 应改用 `engine.get_status()` 封装 API（审阅意见 #9）。
> MSLM 侧需提供 `get_status()` 方法，避免 provider 直接依赖内部数据库 schema。

```
名称:       slm_status
描述:       查看本地记忆库状态。
参数:       无

返回格式:
  {
    "profile": "coder",
    "mode": "A",
    "facts": {"total": 12847},  # v1: total only; v2: + active/warm/cold/archived breakdown
    "entities": 3840,
    "graph_edges": 12300,
    "db_size_mb": 856,
    "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
    "embedding_dim": 768
  }
```

> **注意**：`facts` 中的 active/warm/cold/archived 细分需要按 lifecycle 分组查询。
> 如果单次 status 调用开销过大，v1 只返回 total（一次 `get_fact_count()`），细分统计留到 v2。

### 5.4 `slm_report_feedback`

> **设计参考**：Holographic provider 已有 `fact_feedback` 工具（helpful/unhelpful + fact_id），
> MSLM 的 MCP 工具集中有 `report_feedback`。此工具遵循同样的模式——模型使用事实后反馈其有用性。

```
名称:       slm_report_feedback
描述:       反馈某条记忆事实是否有用。系统根据反馈调整该事实的信任度和检索权重。
参数:
  fact_id    string   required  - 要反馈的事实 ID（来自 slm_recall 返回的 fact_id）
  helpful    boolean  required  - true=有帮助, false=无帮助

返回格式:
  {
    "status": "ok",
    "fact_id": "a1b2c3d4e5f6g7h8",
    "helpful": true
  }
```

> **注意**：v1 中 `slm_recall` 返回 `fact_id`，模型可据此调用 `slm_report_feedback`。
> MSLM engine 目前不直接暴露 `report_feedback` 的 Python API——
> 需要通过 `engine.db` 直接操作 trust 表，或调用 MCP 工具路径。
> 
> **审阅意见 #4**：v1 砍掉 `slm_report_feedback`，只保留 3 个核心工具（recall/remember/status）。
> 原因：
> - 规格第 85 行明确说"不做 REST API 方案"，CLI 桥接与之矛盾
> - CLI 调用有进程开销和序列化成本，`subprocess` 在超时/错误处理上脆弱
> - MSLM 侧需先暴露 Python API，provider 才能可靠实现
> 
> **决策**：v1 移除 `slm_report_feedback`，v2 待 MSLM 暴露 `engine.report_feedback()` 后恢复。

---

## 6. 配置设计

### 6.1 配置来源优先级（决策 3: A+B）

```
1. Hermes config.yaml:  memory.superlocalmemory.<key>     ← 最高优先级
2. MSLM 原生:          ~/.superlocalmemory/config.json    ← 回退
3. 代码默认值                                              ← 最终回退
```

### 6.2 `get_config_schema()`

```python
def get_config_schema(self) -> List[Dict[str, Any]]:
    return [
        {
            "key": "mslm_profile",
            "description": "MSLM profile name. Leave empty to auto-detect from Hermes profile.",
            "required": False,
            "default": "",
        },
        {
            "key": "mode",
            "description": "MSLM operating mode: A (fully local, zero external API), "
                           "B (local Ollama for fact extraction), C (cloud LLM for best quality).",
            "choices": ["A", "B", "C"],
            "default": "A",
        },
        {
            "key": "include_global",
            "description": "Include global-scope facts in search results (cross-profile shared knowledge).",
            "type": "boolean",
            "default": True,
        },
        {
            "key": "include_shared",
            "description": "Include shared-scope facts in search results (agent-to-agent memory).",
            "type": "boolean",
            "default": False,
        },
    ]
```

### 6.3 config.yaml 示例

```yaml
# ~/.hermes/config.yaml
memory:
  provider: superlocalmemory
  superlocalmemory:
    mslm_profile: ""        # 空 = 自动使用 Hermes profile 名
    mode: "A"               # 完全本地
    include_global: true
    include_shared: false
```

### 6.4 plugin.yaml

> **Provider 随 MSLM 发布**，因此 Hermes 的 plugin.yaml 负责声明注册入口，
> 实际 pip 依赖由 MSLM 的 pyproject.toml 管理。

```yaml
name: superlocalmemory
version: 1.0.0
description: "MSLM — 信息几何学的本地 AI 记忆引擎，7通道检索，三层作用域，完全本地运行"
pip_dependencies:
  - mslm-memory>=4.0.0
hooks:
  - on_session_end
  - on_memory_write
  - on_pre_compress
  - on_session_switch
```

---

## 7. 关键实现细节

### 7.1 Engine 初始化

**Hermes 配置读取**：`_load_hermes_config()` 从 Hermes config.yaml 中读取 `memory.superlocalmemory`
section 的覆盖值。

```python
def _load_hermes_config(self, hermes_home: str) -> Dict[str, str]:
    """Read memory.superlocalmemory overrides from Hermes config.yaml."""
    try:
        from hermes_cli.config import load_config, cfg_get
        config = load_config()
        mem_config = config.get("memory", {}) if config else {}
        return mem_config.get("superlocalmemory", {}) if isinstance(mem_config, dict) else {}
    except Exception:
        return {}

def _parse_bool(value: Any, default: bool) -> bool:
    """Parse a boolean value from YAML config, handling string forms like 'false'/'true'."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)
```

**初始化完整流程**：

```python
def initialize(self, session_id: str, **kwargs) -> None:
    # 1. 解析 MSLM profile
    hermes_home = kwargs.get("hermes_home", "~/.hermes")
    agent_identity = kwargs.get("agent_identity", "default")
    config_override = self._load_hermes_config(hermes_home)
    self._mslm_profile = (
        config_override.get("mslm_profile")  # Hermes config 覆盖（非空时生效）
        or agent_identity                     # auto: Hermes profile 名
        or "default"                          # 最终回退
    )

    # 2. 加载 MSLM 配置
    try:
        self._slm_config = SLMConfig.load()
    except Exception:
        logger.warning("MSLM config load failed — provider disabled")
        return
    self._slm_config.active_profile = self._mslm_profile
    mode_override = config_override.get("mode")
    if mode_override:
        try:
            self._slm_config.mode = Mode[mode_override]
        except KeyError:
            logger.warning("MSLM unknown mode '%s' — using config default", mode_override)

    # 3. 读取召回配置（显式处理 YAML 字符串布尔值，避免 "false" 被当 truthy）
    self._include_global = _parse_bool(config_override.get("include_global"), True)
    self._include_shared = _parse_bool(config_override.get("include_shared"), False)

    # 4. cron 守卫
    agent_context = kwargs.get("agent_context", "primary")
    platform = kwargs.get("platform", "cli")
    if agent_context in {"cron", "flush"} or platform == "cron":
        self._cron_skipped = True
        logger.debug("MSLM skipped: cron/flush context")
        return

    # 5. 创建并初始化引擎（带超时保护）
    try:
        self._engine = MemoryEngine(self._slm_config)
        # engine.initialize() 加载 sentence-transformers 模型，可能耗时 5-15s
        # 使用线程 + timeout 防止磁盘 IO 慢或模型下载导致无限阻塞
        import threading
        init_error: Optional[Exception] = None

        def _do_init():
            nonlocal init_error
            try:
                if getattr(self, '_init_cancelled', False):
                    return
                self._engine.initialize()
            except Exception as e:
                init_error = e

        init_thread = threading.Thread(target=_do_init, daemon=True)
        init_thread.start()
        init_thread.join(timeout=30.0)  # 30s timeout
        if init_thread.is_alive():
            logger.warning("MSLM engine init timed out after 30s — provider disabled")
            # 超时后显式清理：设置取消标志，等待线程结束，释放模型加载占用的内存
            # 避免 daemon 线程在后台继续运行导致内存泄漏（审阅意见 #3）
            self._init_cancelled = True
            init_thread.join(timeout=5.0)  # 再给 5s 优雅退出
            if init_thread.is_alive():
                logger.warning("MSLM init thread did not terminate gracefully — may retain model RAM")
            self._engine = None
            return
        if init_error:
            raise init_error
    except Exception as e:
        logger.warning("MSLM engine init failed: %s — provider disabled", e)
        self._engine = None
        return

    # 6. 创建 speaker entities
    # 注意：MSLM 已全面支持 personal/global/shared 三层 scope，
    # 但 entity_resolver._create_entity() 硬编码 scope="global"（不暴露 scope 参数）
    # 对于 Hermes 单用户场景，speaker entities 应为 personal scope
    # v1: 接受当前 global 行为（跨 profile 的 user/hermes 实体）；v2: 修改 _create_entity 接受 scope 参数
    try:
        self._engine.create_speaker_entities("user", "hermes")
    except Exception as e:
        logger.debug("MSLM create_speaker_entities failed (non-fatal): %s", e)

    self._session_id = session_id
    logger.info("MSLM provider ready — profile=%s mode=%s",
                self._mslm_profile, self._slm_config.mode.name)
```

### 7.2 prefetch 混合模式（决策 5: C）

```
Turn 1 (冷启动):
  prefetch(query)
    → 同步 engine.recall(query, limit=8, fast=True)  ← 阻塞，超时 8s
    → 格式化结果
    → 返回结果

Turn 2+ (热路径):
  prefetch(query)
    → 消费 _prefetch_cache（上一轮 queue_prefetch 的结果）
    → 返回缓存结果

queue_prefetch(query) (每轮结束后):
  → 后台 daemon thread:
      engine.recall(query, limit=8, fast=True)
      → 结果写入 self._prefetch_cache
```

```
Timeline:

Turn 1: [prefetch同步获取] [模型调用...] [sync_turn] [queue_prefetch启动后台]
Turn 2:                [prefetch消费缓存] [模型调用...] [sync_turn] [queue_prefetch启动后台]
Turn 3:                                  [prefetch消费缓存] [模型调用...]

> **queue_prefetch 并发安全说明**：
> `queue_prefetch` 启动的后台线程调用 `engine.recall()`，虽然读操作在 WAL 模式下可以并发，
> 但如果 `recall` 内部有写（如更新访问时间戳、缓存统计），会和 `_write_lock` 保护的写冲突。
> 需确认 MSLM `engine.recall()` 是否为纯读操作；如有写，需要纳入锁管理或明确文档（审阅意见 #2）。
```

### 7.3 sync_turn 合并存储（决策 2: A）

```python
def sync_turn(self, user_content: str, assistant_content: str,
              *, session_id: str = "") -> None:
    if self._cron_skipped or not self._engine:
        return

    clean_user = sanitize_context(user_content or "").strip()
    clean_asst = sanitize_context(assistant_content or "").strip()

    # 太短的回合跳过（如 "ok", "yes", "thanks"）
    # 改为语义过滤：非空且非纯标点/空白，保留 "no"、"fix" 等简短但有意义的内容
    # 审阅意见 #5：3 字符阈值会漏掉很多有意义的信息
    if not clean_user or clean_user.strip() in {"", "ok", "yes", "thanks", "thx"}:
        return

    combined = f"User: {clean_user}\nHermes: {clean_asst}"

    def _sync():
        try:
            with self._write_lock:  # 串行化所有写操作
                self._engine.store(
                    combined,
                    session_id=session_id or self._session_id,
                    speaker="user",
                    scope="personal",
                )
        except Exception as e:
            logger.debug("MSLM sync_turn failed: %s", e)

    # 丢弃上一轮尚未完成的写入（daemon thread 保护，不阻塞主线程）
    # 使用 _sync_turn_lock 保护 is_alive() 检查和 thread.start() 之间的竞态条件，
    # 防止两个 turn 连续快速触发时同时创建两个写线程。
    with self._sync_turn_lock:
        if self._sync_thread and self._sync_thread.is_alive():
            logger.debug("MSLM sync_turn: prior write still in progress, dropping")
            return
        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="mslm-sync"
        )
        self._sync_thread.start()
```

> **写并发策略变更**：原来的 `join(timeout=5.0)` 方案在上一个写入超过 5s 时会导致两个
> thread 并发写 SQLite，在 WAL 模式下可能触发 `database is locked`。改为：
> - 提供一个 `threading.Lock`（`self._write_lock`）保护所有 `engine.store()` 调用
> - 如果上一轮写入尚未完成，跳过本轮的 `sync_turn`（而非堆积写入队列）
> - `on_memory_write`、`on_pre_compress` 也共用同一把锁
> - 新增 `self._sync_turn_lock` 保护 `is_alive()` 检查和 `thread.start()` 之间的竞态窗口，
>   防止两个 turn 连续快速触发时同时创建两个写线程（审阅意见 #1）

### 7.4 线程安全

MSLM 的 SQLite 使用 WAL 模式：
- 读操作（`recall`）可以并发
- 写操作（`store`）需串行化（WAL 支持一写多读，但多个并发写会触发 `database is locked`）

Provider 内部状态：

```python
self._engine: Optional[MemoryEngine] = None    # 主引擎实例
self._write_lock = threading.Lock()            # 保护所有 engine.store() 调用
self._sync_turn_lock = threading.Lock()        # 保护 sync_turn 的 is_alive() 检查和 thread 创建
self._sync_thread: Optional[threading.Thread]  # sync_turn 后台线程
self._prefetch_thread: Optional[threading.Thread]
self._prefetch_lock: threading.Lock            # 保护 prefetch 缓存读写
self._prefetch_cache: str = ""                 # 预取结果
self._prefetch_fired_at: int = -999            # 缓存对应的 turn 号
```

所有写入路径（`sync_turn`、`on_memory_write`、`on_pre_compress`、工具 `slm_remember`）
通过 `self._write_lock` 串行化。读操作（`prefetch`、工具 `slm_recall`）不需要锁。

### 7.5 错误处理

| 场景 | 策略 |
|------|------|
| `SLMConfig.load()` 失败 | `initialize()` 静默 return，provider 不激活 |
| `engine.initialize()` 超时 (>30s) | 记录 warning，设置取消标志，等待线程结束，`self._engine = None`，provider 不激活 |
| `engine.initialize()` 抛异常 | 记录 warning，`self._engine = None`，provider 不激活 |
| `engine.recall()` 失败 | 返回空字符串，记录 debug 日志 |
| `engine.store()` 失败 | 记录 debug 日志，不影响主流程 |
| engine 变为 None (运行时崩溃) | 所有方法检查 `self._engine is not None` |
| 超长内容 (>4000 chars) | sync_turn 中截断到 4000 字符：MSLM fact_extractor 处理超长文本时提取质量下降（噪声事实增加），4000 字符覆盖 >95% 的正常对话轮次 |
| 空查询 | prefetch 直接返回 "" |
| `create_speaker_entities()` 失败 | 非致命错误，记录 debug 日志后继续 |
| **工具调用异常** | `handle_tool_call` 捕获异常，返回 `tool_error` 结构，不中断主流程（审阅意见 #12） |

---

## 8. 文件清单

> **Provider 随 MSLM 发布**，文件位于 MSLM 仓库内。

```
superlocalmemory/
├── src/superlocalmemory/
│   └── integrations/
│       └── hermes/
│           ├── __init__.py        # SuperLocalMemoryProvider 实现 (~450 行)
│           └── plugin.yaml        # 元数据清单
```

### 8.1 `__init__.py` 结构

```
├── 模块 docstring
├── imports
├── 常量: TOOL_SCHEMAS, TIMEOUT, 等
├── class SuperLocalMemoryProvider(MemoryProvider):
│   ├── __init__()              # 字段声明
│   ├── name (property)         # "superlocalmemory"
│   ├── is_available()          # import 检查
│   ├── get_config_schema()     # 配置暴露
│   ├── initialize()            # engine 初始化 + config 解析
│   ├── system_prompt_block()   # 状态文本
│   ├── prefetch()              # 混合模式
│   ├── queue_prefetch()        # 后台预取
│   ├── sync_turn()             # 合并存储
│   ├── on_memory_write()       # 内置 memory 镜像
│   ├── on_session_end()        # close_session
│   ├── on_pre_compress()       # 压缩前摘要存储
│   ├── on_session_switch()     # 缓存重置
│   ├── get_tool_schemas()      # 4 个工具
│   ├── handle_tool_call()      # 路由到 _tool_*
│   ├── _tool_recall()          # slm_recall 实现
│   ├── _tool_remember()        # slm_remember 实现
│   ├── _tool_status()          # slm_status 实现
│   ├── _tool_report_feedback() # slm_report_feedback 实现
│   ├── _format_recall_results()# 结果格式化
│   ├── _ensure_engine()        # engine 健康检查
│   ├── _sync_recall()          # 同步 recall 包装
│   └── shutdown()              # 清理
└── def register(ctx)           # 插件入口
```

---

## 9. 与现有 Provider 的对比

| 特性 | Honcho | OpenViking | **SuperLocalMemory (MSLM)** |
|------|--------|-----------|---------------------|
| 部署方式 | Cloud / Self-host | Docker / Server | **本地进程内** |
| 检索方式 | LLM dialectic | REST API | **7通道 + RRF** |
| 嵌入模型 | 服务端 | 服务端 | **本地 CPU** |
| 离线可用 | ❌ | ❌ | **✅** |
| 隐私 | 数据上传 | 数据上传 | **全本地** |
| 额外依赖 | honcho-ai SDK | httpx + docker | **仅 mslm-memory 包** |
| 作用域 | workspace 隔离 | account/user 隔离 | **profile + 3层 scope** |

---

## 10. 待实现

- [ ] v1: 核心 MemoryProvider（3 工具 + 完整生命周期）
- [ ] v1: `hermes memory setup` 集成
- [ ] v1: 确认 `engine.recall()` 是否为纯读（审阅意见 #2）
- [ ] v2: `slm_report_feedback` 恢复（待 MSLM 暴露 Python API）
- [ ] v2: `shared` scope 支持（多 agent mesh）
- [ ] v2: `on_delegation` 钩子（子 agent 观察）
- [ ] v2: MSLM 侧 `_create_entity()` 接受 scope 参数（speaker entities 改为 personal）
- [ ] v2: MSLM 侧 `engine.get_status()` 封装 API（审阅意见 #9）
- [ ] v2: `speaker="both"` 支持或拆分成两次 `store()`（审阅意见 #10）
- [ ] v3: 连接方式支持 REST API fallback

---

## 附录 A: `engine.store()` 行为确认

`run_store()`（`engine.store()` 的实际实现）在一次同步调用中完成以下所有步骤：

```text
engine.store(content, session_id, speaker, scope)

  ┌─ ① db.store_memory(record)
  │     └─ 原始对话文本 → memories 表（永久保留）
  │
  ├─ ② fact_extractor.extract_facts(content)
  │     └─ 从文本中提取原子事实列表（同步，非 daemon 异步）
  │
  ├─ ③ 兜底策略:
  │     ├─ V3.3.11: 额外存储 verbatim 副本作为事实（保留细节）
  │     └─ V3.3.21: 如果 extract_facts 返回空 → 直接存原文为最小事实
  │
  ├─ ④ for each fact: 同步 enrich
  │     ├─ embedder.embed()            → 向量嵌入
  │     ├─ embedder.compute_fisher_params() → Fisher 均值/方差
  │     ├─ entity_resolver.resolve()   → 实体链接
  │     ├─ temporal_parser             → 时间解析
  │     ├─ emotional tag               → 情感标注
  │     └─ signal inference            → 信号类型推断
  │
  ├─ ⑤ for each fact: 同步持久化
  │     ├─ db.store_fact(fact)         → atomic_facts 表
  │     ├─ vector_store.upsert()       → 向量索引
  │     ├─ db.store_edge()             → 实体图谱边
  │     ├─ db.store_temporal_validity()→ 时间有效性
  │     └─ db.store_temporal_event()   → 时间线事件
  │
  └─ ⑥ 返回 fact_ids[]
```

**结论**：一次 `store()` 调用同时完成原始对话保留（`memories` 表）和结构化事实提取（`atomic_facts` 表 + 向量 + 图谱）。无需额外 daemon 参与 initial extraction。Daemon 负责的是后续的周期性维护（Ebbinghaus 遗忘衰减、Langevin 动力学、认知巩固 CCQ、场景聚类等）。

因此在 `sync_turn` 中单次 `engine.store()` 调用即充分覆盖所有需求。

---

## 附录 B: 修正记录

| 日期 | 修改 |
|------|------|
| 2026-06-01 | 初版评审：修正包名（`mslm-memory`）、布尔类型配置、sync_turn 阈值、4000 截断理由、事实分组 v1/v2 |
| 2026-06-01 | `slm_report_feedback` 加入 v1 工具（参考 Holographic fact_feedback） |
| 2026-06-01 | 标注 `create_speaker_entities` scope 问题（硬编码 global）和 ByteRover 10 条消息先例 |
| 2026-06-02 | Provider 随 MSLM 发布（`src/superlocalmemory/integrations/hermes/`），文件清单更新 |
| 2026-06-22 | 审阅意见修复（12条）：线程安全（`_sync_turn_lock`）、`queue_prefetch` 并发说明、init 超时清理（`_init_cancelled`）、`slm_report_feedback` 移至 v2、语义过滤替代字符阈值、`on_pre_compress` 返回值说明、`_parse_bool` 类型安全、`system_prompt_block()` 文本补充、`engine.db` 封装说明、`speaker` 字段说明、plugin.yaml 钩子补全、工具调用异常处理 |
