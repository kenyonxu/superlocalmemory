# Hermes Agent 快速上手指南
> SuperLocalMemory V3 Documentation
> https://superlocalmemory.com | Part of Qualixar

本指南面向 [Hermes Agent](https://github.com/3rdparty/hermes) 用户，介绍如何通过 MCP 协议将 SuperLocalMemory (SLM) 集成到 Hermes Agent 中，实现持久化记忆。

---

## 前置条件

- Python 3.11+
- Hermes Agent 已安装并支持 MCP 协议
- 操作系统：macOS / Linux / Windows

---

## 1. 安装 SuperLocalMemory

```bash
pip install superlocalmemory
```

验证安装：
```bash
slm --version
slm doctor
```

---

## 2. 首次配置

```bash
slm setup
```

配置向导会引导选择运行模式：

| 模式 | 名称 | 说明 | 推荐场景 |
|------|------|------|---------|
| **A** | Local Guardian | 零云端、零 LLM，纯本地处理 | 隐私优先、离线环境 |
| **B** | Smart Local | 使用本地 Ollama LLM | 数据不出局域网 |
| **C** | Full Power | 使用云端 LLM | 最高检索质量 |

**Hermes Agent 用户推荐选择 Mode A** — 无需额外依赖即可工作。

---

## 3. Hermes Agent 接入 SLM

SLM 通过 MCP 协议对外提供服务。启动命令：

```bash
slm mcp
```

### 3.1 MCP 配置

在 Hermes Agent 的 MCP 配置文件中添加 SLM 服务。

**配置文件位置**（取决于 Hermes 版本）：
- `~/.config/hermes/mcp.json`
- 或 Hermes Agent 设置界面中的 MCP 配置页

**配置模板**：

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "slm",
      "args": ["mcp"],
      "env": {
        "SLM_DATA_DIR": "/home/YOURNAME/.superlocalmemory",
        "SLM_MODE": "a",
        "SLM_MCP_ALL_TOOLS": "0"
      }
    }
  }
}
```

### 3.2 环境变量说明

| 变量 | 说明 | 推荐值 |
|------|------|--------|
| `SLM_DATA_DIR` | 数据存储目录 | `~/.superlocalmemory` |
| `SLM_MODE` | 运行模式：`a` / `b` / `c` | `a` |
| `SLM_MCP_ALL_TOOLS` | `1` 启用全部 75 个工具，`0` 仅 33 个核心工具 | `0` |
| `SLM_MCP_MESH_TOOLS` | `1` 启用 8 个 Mesh P2P 工具 | 按需 |

> **提示**：Hermes Agent 对工具数量有限制时，建议保持 `SLM_MCP_ALL_TOOLS=0`，仅使用 33 个核心工具。

### 3.3 验证连接

在 Hermes Agent 中测试：

```
请调用 remember 工具存储："我正在使用 Hermes Agent"
```

然后测试检索：

```
请调用 recall 工具搜索："我用的是什么 Agent"
```

返回正确结果即表示集成成功。

---

## 4. 核心工具速查

SLM 注册到 Hermes Agent 后，可直接调用以下工具：

### 记忆操作

| 工具 | 功能 | 示例 |
|------|------|------|
| `remember` | 存储记忆 | "记住项目 Phoenix 使用 React 18" |
| `recall` | 语义检索 | "Phoenix 项目的技术栈是什么" |
| `search` | 搜索记忆 | "搜索所有关于数据库的记忆" |
| `fetch` | 按 ID 获取 | "获取记忆 ID abc123" |
| `list_recent` | 列出最近记忆 | "显示最近 10 条记忆" |
| `delete_memory` | 删除记忆 | "删除记忆 abc123" |
| `update_memory` | 更新记忆 | "更新记忆 abc123 的内容" |

### 会话与上下文

| 工具 | 功能 |
|------|------|
| `session_init` | 初始化新会话 |
| `observe` | 让 SLM 观察当前上下文 |
| `report_feedback` | 反馈记忆有用/无用 |
| `get_status` | 获取系统状态 |

### 实体管理

| 工具 | 功能 | 示例 |
|------|------|------|
| `merge_entities` | 合并重复实体      | 合并个人实体到全局实体            |

### 在 Hermes Agent 中使用示例

直接发送自然语言指令即可：

> 调用 remember 存储："用户偏好 TypeScript + Tailwind，项目代号 Phoenix，部署在 Vercel"

> 调用 recall："Phoenix 项目部署在哪里"

> 调用 observe 分析这段代码：[粘贴代码片段]

---

## 5. 三层作用域记忆（Multi-Scope Memory）

SLM V3.4+ 引入三层作用域记忆架构，让 Hermes Agent 团队中的多个 Agent 既能保持各自的记忆空间，又能共享公共知识。

### 5.1 三层作用域模型

```
┌──────────────────────────────────────────────────────┐
│  Global（全局）                                       │
│  全员共享的技术实体：React、Python、Docker...         │
│  新创建的实体默认存入此层                              │
│  RRF 权重：0.5（可通过 config.json 调整）             │
├──────────────────────────────────────────────────────┤
│  Shared（指定共享）                                   │
│  与特定 Agent 共享的记忆                              │
│  通过 shared_with 参数指定共享对象                    │
│  RRF 权重：0.7（可通过 config.json 调整）             │
├──────────────────────────────────────────────────────┤
│  Personal（个人）                                     │
│  仅当前 Agent 可见的私有记忆                          │
│  RRF 融合中权重最高（1.0，可通过 config.json 调整）   │
└──────────────────────────────────────────────────────┘
```

**检索优先级**：personal > shared > global。RRF 融合时，个人记忆排在前面，全局记忆排在后面。权重值可在 `config.json` 的 `scope_weights` 字段中自定义（默认 personal=1.0, shared=0.7, global=0.5）。

### 5.2 全局权威实体

所有 Agent 共享同一套技术实体（React、Python、Kubernetes 等），不再各自创建重复的实体副本。

- 新创建的实体**默认存入 global scope**
- 任意 Agent 都可以创建全局实体
- 所有 Agent 的 `recall` 自动包含全局实体的关联记忆
- 实体别名和模糊匹配也支持跨 scope 查找

### 5.3 域标签（Domain Tags）

SLM 自动将实体映射到技术领域（frontend / backend / devops / mobile / data），用于跨 Agent 的领域匹配：

- 规则引擎：内置 48 个常见实体→领域映射（React→frontend, Docker→devops 等）
- LLM 回退：未命中规则的实体，自动调用 LLM 分类并缓存结果
- 域标签用于 `shared` scope 的跨 Agent 匹配——领域重叠的 Agent 自动共享相关记忆

### 5.4 在 Hermes Agent 中使用

**存储记忆时指定 scope**：

```
调用 remember，存储 "项目 Phoenix 使用 React 18"，scope 设为 "personal"
```

```
调用 remember，存储 "团队约定使用 TypeScript strict 模式"，scope 设为 "global"
```

```
调用 remember，存储 "后端 API 规范 v2"，scope 设为 "personal"，shared_with 设为 "backend_agent,frontend_agent"
```

| `scope` 值 | 含义 | 可见范围 |
| --- | --- | --- |
| `personal` | 仅自己可见 | 当前 profile_id |
| `global` | 全员可见 | 所有 Agent |
| `personal` + `shared_with` | 指定共享 | profile_id + 列表中的 Agent |

**检索记忆时控制范围**：

```
调用 recall，查询 "React 技术栈"，include_global 设为 true
```

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `include_global` | 是否包含全局 scope 的记忆 | `true` |
| `include_shared` | 是否包含共享 scope 的记忆 | `true` |

默认两个参数都为 `true`，即检索结果自动包含三层作用域的记忆。

### 5.5 多 Agent 协作示例

**场景**：Agent A（zhihui）存储了一条关于 React 的记忆，Agent B（xiaoming）可以自动检索到。

```
# Agent A 存储
调用 remember：存储 "React 18 支持并发特性"，scope = "global"

# Agent B 检索（自动找到 Agent A 存储的全局记忆）
调用 recall：查询 "React 有什么新特性"
→ 返回 Agent A 存储的 "React 18 支持并发特性"
```

**关键特性**：
- 全局实体（如 React）只创建一次，所有 Agent 共享同一实体 ID
- 通过全局实体关联的知识图谱边、域标签等也自动共享
- 无需手动同步——检索时 RRF 融合自动合并三层结果

---

## 6. 用户画像（Profiles）

SLM 支持多画像隔离，适合在 Hermes Agent 中切换不同工作上下文：

```bash
# 命令行管理
slm profile list              # 列出所有画像
slm profile create work       # 创建工作画像
slm profile switch work       # 切换到工作画像
```

在 Hermes Agent 中，通过 `session_init` 工具的 `profile_id` 参数指定：

```
调用 session_init，profile_id 设为 "work"
```

**Profile 与 Scope 的关系**：
- `profile_id` = 谁的记忆（身份隔离）
- `scope` = 记忆的可见范围（层级隔离）
- 两者正交组合：同一 profile 可以有 personal / global / shared 三种记忆

---

## 7. 后台守护进程（推荐）

启动守护进程可消除冷启动延迟（从 ~23 秒降至即时响应）：

```bash
slm serve start     # 后台运行（~600MB RAM）
slm serve status    # 查看状态
slm serve stop      # 停止
```

或一键重启：
```bash
slm restart         # 杀孤儿进程 + 清理 + 重启 + 健康检查
```

---

## 8. Web 仪表盘

```bash
slm dashboard       # 打开 http://localhost:8765
```

仪表盘提供：
- 记忆网络图可视化
- 检索统计与性能
- 系统健康检查
- 记忆维护操作（整合、衰减、量化）

---

## 9. 日常 CLI 速查

```bash
# 最常用
slm remember "内容" --tags "标签1,标签2"
slm recall "查询" --limit 10
slm list -n 20
slm status

# 维护
slm consolidate --cognitive   # 认知整合（去重合并）
slm decay --execute           # 遗忘曲线衰减
slm quantize --execute        # 嵌入量化（节省空间）

# 实体管理
slm entity list --scope personal   # 列出个人作用域的实体
slm entity merge <源ID> <目标ID>   # 合并重复实体（源被删除，目标保留）

# 诊断
slm doctor
slm health
```

---

## 10. 故障排查

| 现象 | 排查步骤 |
|------|---------|
| 冷启动慢 | 先运行 `slm serve start` 启动守护进程 |
| 检索质量差 | 运行 `slm health` 检查 Fisher-Rao / Sheaf 状态 |
| 工具调用失败 | 检查 `slm doctor` 输出，确认 MCP 服务器正常 |
| 数据目录冲突 | 确认 `SLM_DATA_DIR` 环境变量指向正确位置 |

---

## 参考

- [MCP Tools 完整参考](mcp-tools.md)
- [CLI 参考](cli-reference.md)
- [用户画像](profiles.md)
- [故障排查](troubleshooting.md)
