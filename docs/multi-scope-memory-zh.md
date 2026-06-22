[中文](./multi-scope-memory-zh.md) | [English](./multi-scope-memory-en.md)

# MSLM 多层次结构记忆

> MSLM 的核心特性：通过三层作用域（personal / shared / global）实现记忆的灵活隔离与共享。
> 多个 AI Agent 既能保持各自的私有记忆，又能共享团队知识和全局实体。

---

## 1. 三层作用域模型

MSLM 引入独立的 `scope` 维度，与用户画像（`profile_id`）正交组合：

```
┌──────────────────────────────────────────────────────┐
│  Global（全局）                                       │
│  全员共享的技术实体：React、Python、Docker...         │
│  新创建的实体默认存入此层                              │
│  RRF 权重：0.5                                        │
├──────────────────────────────────────────────────────┤
│  Shared（指定共享）                                   │
│  与特定 Agent 共享的记忆                              │
│  通过 shared_with 参数指定共享对象                    │
│  RRF 权重：0.7                                        │
├──────────────────────────────────────────────────────┤
│  Personal（个人）                                     │
│  仅当前 Agent 可见的私有记忆                          │
│  RRF 融合中权重最高（1.0）                            │
└──────────────────────────────────────────────────────┘
```

**设计原则**：
- `profile_id` = 用户身份（技术栈、偏好、工作习惯）
- `scope` = 可见性边界（个人 / 共享 / 全局）
- 两者正交：同一 profile 可以有 personal / shared / global 三种记忆

**检索优先级**：personal > shared > global。RRF 融合时，个人记忆排在前面，全局记忆排在后面。权重值可在 `config.json` 的 `scope_weights` 字段中自定义。

---

## 2. 跨作用域检索

当用户查询时，MSLM 自动并行检索三层作用域，然后通过 RRF（Reciprocal Rank Fusion）合并排序：

```
query ──┬──► personal scope (profile_id 隔离)
        │    └── 仅返回当前用户的个人记忆
        │
        ├──► shared scope (shared_with 匹配)
        │    └── 返回与当前 Agent 共享的记忆
        │
        └──► global scope (无 profile 限制)
             └── 返回全员可见的全局记忆

              ↓
        [RRF 加权融合] ──► 跨 scope 排序 ──► 返回 Top-K
```

**默认权重**（可在 `config.json` 中调整）：

| Scope | 默认权重 | 说明 |
|-------|:-------:|------|
| `personal` | 1.0 | 最优先，个人记忆排最前 |
| `shared` | 0.7 | 共享记忆次之 |
| `global` | 0.5 | 全局记忆权重最低 |

---

## 3. 全局权威实体

所有 Agent 共享同一套技术实体（React、Python、Kubernetes 等），不再各自创建重复的实体副本。

- 新创建的实体**默认存入 global scope**
- 任意 Agent 都可以创建全局实体
- 所有 Agent 的 `recall` 自动包含全局实体的关联记忆
- 实体别名和模糊匹配也支持跨 scope 查找

### 域标签（Domain Tags）

MSLM 自动将实体映射到技术领域（frontend / backend / devops / mobile / data），用于跨 Agent 的领域匹配：

- **规则引擎**：内置 48 个常见实体→领域映射（React→frontend, Docker→devops 等）
- **LLM 回退**：未命中规则的实体，自动调用 LLM 分类并缓存结果
- 域标签用于 `shared` scope 的跨 Agent 匹配——领域重叠的 Agent 自动共享相关记忆

---

## 4. 在 MCP 工具中使用

### 存储记忆时指定 scope

**remember 工具参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `content` | string | 必填 | 要存储的内容 |
| `scope` | string | `"personal"` | 作用域：`personal` / `global` / `shared` |
| `shared_with` | string | `""` | 仅在 `scope="shared"` 时有效，逗号分隔的 Agent ID |
| `tags` | string | `""` | 逗号分隔的标签 |
| `importance` | int | `5` | 重要性评分（1-10） |
| `session_id` | string | `""` | 会话 ID，用于关联同一对话的记忆 |

**示例**：

```
# 个人记忆
调用 remember，存储 "用户偏好 TypeScript + Tailwind"，scope 设为 "personal"

# 全局知识
调用 remember，存储 "团队约定使用 TypeScript strict 模式"，scope 设为 "global"

# 指定共享
调用 remember，存储 "后端 API 规范 v2"，scope 设为 "shared"，shared_with 设为 "backend_agent,frontend_agent"
```

### 检索记忆时控制范围

**recall 工具参数**：

| 参数 | 说明 | 默认值 |
|------|------|:------:|
| `query` | 查询文本（必填） | — |
| `limit` | 返回条数 | `10` |
| `include_global` | 是否包含 global scope | `true` |
| `include_shared` | 是否包含 shared scope | `true` |

> **注意**：当前版本 `include_global` / `include_shared` 参数已被接受，但检索结果始终包含三层 scope 的记忆。精细的逐 scope 过滤将在后续版本中启用。

### CLI 命令

```bash
# 存储不同作用域的记忆
mslm remember "个人偏好：喜欢函数式编程" --scope personal
mslm remember "React 18 支持并发特性" --scope global
mslm remember "内部 API 密钥轮换规则" --scope shared --shared-with "backend_agent,frontend_agent"

# 查看不同作用域的实体
mslm entity list --scope personal
mslm entity list --scope shared
mslm entity list --scope global

# 合并重复实体
mslm entity merge <源ID> <目标ID>
```

### Hermes MemoryProvider

使用 Hermes MemoryProvider 插件时，scope 通过工具参数控制：

```python
# 存储不同作用域的记忆
slm_remember("个人偏好：喜欢函数式编程", scope="personal")
slm_remember("React 18 支持并发特性", scope="global")
slm_remember("内部 API 密钥轮换规则", scope="shared", shared_with="backend_agent")

# 检索时控制作用域范围
slm_recall("React 并发特性", include_global=True, include_shared=False)
```

> 详见 [Hermes Agent 集成指南](hermes-agent-guide-zh.md#5-三层作用域记忆multi-scope-memory)

---

## 5. 多 Agent 协作示例

### 场景一：全局知识共享

Agent A（zhihui）存储了一条关于 React 的记忆，Agent B（xiaoming）自动检索到。

```
# Agent A 存储全局知识
调用 remember：存储 "React 18 支持并发特性"，scope = "global"

# Agent B 检索
调用 recall：查询 "React 有什么新特性"
→ 返回 Agent A 存储的 "React 18 支持并发特性"
```

### 场景二：指定共享敏感信息

Agent A 只想与特定 Agent 共享敏感信息（如 API 密钥规范）。

```
# Agent A 存储共享记忆
调用 remember：存储 "内部 API 密钥轮换规则：每 30 天一次"
  scope = "shared"
  shared_with = "backend_agent,frontend_agent"

# Agent B（backend_agent）检索
调用 recall：查询 "API 密钥轮换"
→ 返回 "内部 API 密钥轮换规则：每 30 天一次"

# Agent C（devops_agent）检索
调用 recall：查询 "API 密钥轮换"
→ 无结果（不在 shared_with 列表中）
```

### 场景三：多 Agent 协作初始化

多个 Hermes Agent 同时上线时，预加载共享知识到 global scope：

```python
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.config import SLMConfig

TEAM_KNOWLEDGE = [
    "项目 Phoenix 使用 React 18 + TypeScript + Tailwind CSS",
    "API 网关部署在 Kong，后端是 Go microservices",
    "数据库使用 PostgreSQL 16，ORM 是 Prisma",
    "CI/CD 使用 GitHub Actions，部署到 AWS EKS",
    "代码仓库组织：monorepo，使用 Turborepo 管理",
    "团队编码规范：ESLint + Prettier，严格模式",
]

def bootstrap_team_knowledge(profile_id: str = "default"):
    config = SLMConfig(active_profile=profile_id)
    engine = MemoryEngine(config=config)
    engine.initialize()

    for knowledge in TEAM_KNOWLEDGE:
        engine.store(
            content=knowledge,
            scope="global",
            metadata={"tags": "team,convention", "imported_from": "bootstrap"},
        )

# 为每个 Agent 初始化
for agent in ["zhihui", "xiaoming", "xiaohong"]:
    bootstrap_team_knowledge(agent)
```

---

## 6. Profile 与 Scope 的关系

| 维度 | 含义 | 用途 |
|------|------|------|
| `profile_id` | 谁的记忆 | 身份隔离（不同用户/项目的记忆空间） |
| `scope` | 记忆的可见范围 | 层级隔离（个人/共享/全局） |

两者正交组合：
- 同一 profile 可以有 personal、shared、global 三种记忆
- 不同 profile 的 personal 记忆完全隔离
- 不同 profile 共享同一个 global 实体空间

```bash
# 管理 profile
mslm profile list              # 列出所有画像
mslm profile create work       # 创建工作画像
mslm profile switch work       # 切换到工作画像
```

在 Hermes Agent 中通过 `session_init` 工具指定：

```
调用 session_init，profile_id 设为 "work"
```

---

## 7. 关键特性总结

- **全局实体共享**：React、Python 等技术实体只创建一次，所有 Agent 共享同一实体 ID
- **自动跨 scope 检索**：RRF 融合自动合并三层结果，无需手动指定来源
- **灵活的可见性**：personal（私有）、shared（指定共享）、global（全员可见）
- **域标签匹配**：48 个内置实体→领域映射，支持 LLM 回退
- **向后兼容**：未指定 scope 的记忆默认为 `personal`，不影响其他 Agent
- **权重可调**：personal/shared/global 的 RRF 融合权重可在 config.json 中自定义

---

## 参考

- [快速上手指南](getting-started-zh.md) — 从安装到日常使用
- [Hermes Agent 集成指南](hermes-agent-guide-zh.md) — MCP 协议集成
- [记忆导入指南](memory-import-guide-zh.md) — 批量导入到三层作用域
- [配置指南](configuration-zh.md) — scope_weights 等配置项

---

*MSLM (Multi-Scope Local Memory) — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
