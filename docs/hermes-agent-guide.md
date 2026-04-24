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

### 在 Hermes Agent 中使用示例

直接发送自然语言指令即可：

> 调用 remember 存储："用户偏好 TypeScript + Tailwind，项目代号 Phoenix，部署在 Vercel"

> 调用 recall："Phoenix 项目部署在哪里"

> 调用 observe 分析这段代码：[粘贴代码片段]

---

## 5. 用户画像（Profiles）

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

---

## 6. 后台守护进程（推荐）

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

## 7. Web 仪表盘

```bash
slm dashboard       # 打开 http://localhost:8765
```

仪表盘提供：
- 记忆网络图可视化
- 检索统计与性能
- 系统健康检查
- 记忆维护操作（整合、衰减、量化）

---

## 8. 日常 CLI 速查

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

# 诊断
slm doctor
slm health
```

---

## 9. 故障排查

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
