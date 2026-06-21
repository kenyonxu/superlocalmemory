[中文](./configuration-zh.md) | [English](./configuration-en.md)

# MSLM 配置指南

> 控制 MSLM 如何存储、检索和处理你的记忆。

---

## 三种运行模式

MSLM 支持三种运行模式，在隐私与能力之间灵活取舍。

| 模式 | 说明 | 需要 API Key？ | 数据离开本机？ |
|------|------|:------------:|:------------:|
| **A: Zero-Cloud** | 纯数学驱动检索，零 LLM 调用 | 否 | 永不 |
| **B: Local LLM** | Mode A + 本地 Ollama LLM | 否 | 永不 |
| **C: Cloud LLM** | Mode B + 云端 LLM，最高检索质量 | 是 | 是（仅查询） |

### 查看当前模式

```bash
mslm mode
```

### 切换模式

```bash
mslm mode a    # 零云端（默认）
mslm mode b    # 本地 LLM
mslm mode c    # 云端 LLM
```

切换即时生效，不丢失数据。

### Mode A: Zero-Cloud（默认）

所有操作在本地完成。检索使用四条通道（语义相似度、关键词搜索、实体图谱、时间上下文）组合数学评分。无网络调用。

适用场景：隐私敏感工作、离线环境、EU AI Act 合规。

### Mode B: Local LLM

Mode A 全部能力 + 本地 LLM（通过 Ollama）提升查询理解和结果重排。

**配置步骤：**

```bash
# 安装 Ollama（如未安装）
brew install ollama          # macOS
curl -fsSL https://ollama.com/install.sh | sh  # Linux

# 下载模型
ollama pull llama3.2

# 切换到 Mode B
mslm mode b
```

适用场景：希望更好检索质量但不发送数据到云端。

### Mode C: Cloud LLM

Mode B 全部能力 + 云端 LLM（交叉编码器重排、多轮检索）。最高检索质量。

**配置步骤：**

```bash
mslm mode c
mslm provider set openai
```

会提示输入 API Key（存储在本地配置文件，仅发送到你选择的 Provider）。

适用场景：隐私约束允许时追求最高检索质量。

---

## Provider 配置

Mode C 支持多种 LLM Provider。

### 设置 Provider

```bash
mslm provider           # 查看当前 Provider
mslm provider set       # 交互式 Provider 选择器
```

### 支持的 Provider

| Provider | 命令 | 环境变量 |
|----------|------|---------|
| OpenAI | `mslm provider set openai` | `OPENAI_API_KEY` |
| Anthropic | `mslm provider set anthropic` | `ANTHROPIC_API_KEY` |
| Azure OpenAI | `mslm provider set azure` | `AZURE_OPENAI_API_KEY` |
| Ollama (本地) | `mslm provider set ollama` | 不需要 |
| OpenRouter | `mslm provider set openrouter` | `OPENROUTER_API_KEY` |

### 设置 API Key

可通过交互式或环境变量设置：

```bash
# 交互式（存储在配置文件）
mslm provider set openai
# 提示: 输入你的 OpenAI API key: sk-...

# 通过环境变量（优先级更高）
export OPENAI_API_KEY="sk-..."
```

---

## 配置文件

所有设置存储在：

```
~/.superlocalmemory/config.json
```

### 配置示例

```json
{
  "mode": "a",
  "profile": "default",
  "provider": {
    "name": "openai",
    "model": "gpt-4o-mini",
    "api_key_env": "OPENAI_API_KEY"
  },
  "auto_capture": true,
  "auto_recall": true,
  "embedding_model": "all-MiniLM-L6-v2",
  "max_recall_results": 10,
  "retention": {
    "default_policy": "indefinite"
  },
  "scope_weights": {
    "personal": 1.0,
    "shared": 0.7,
    "global": 0.5
  }
}
```

### 关键设置

| 设置 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `"a"` | 运行模式：`a`、`b`、`c` |
| `profile` | `"default"` | 当前活跃的记忆 profile |
| `auto_capture` | `true` | 自动存储决策和上下文 |
| `auto_recall` | `true` | 自动注入相关记忆 |
| `embedding_model` | `"all-MiniLM-L6-v2"` | 语义搜索的 Sentence Transformer 模型 |
| `max_recall_results` | `10` | 每次查询最大返回记忆数 |
| `scope_weights` | `{"personal": 1.0, "shared": 0.7, "global": 0.5}` | 三层作用域 RRF 融合权重 |

---

## 环境变量

以下环境变量会覆盖配置文件中的设置：

| 变量 | 用途 |
|------|------|
| `SLM_MODE` | 覆盖运行模式 |
| `SLM_PROFILE` | 覆盖活跃 profile |
| `SLM_DATA_DIR` | 覆盖数据目录（默认：`~/.superlocalmemory/`） |
| `OPENAI_API_KEY` | Mode C 的 OpenAI API Key |
| `ANTHROPIC_API_KEY` | Mode C 的 Anthropic API Key |
| `AZURE_OPENAI_API_KEY` | Mode C 的 Azure OpenAI API Key |
| `OPENROUTER_API_KEY` | Mode C 的 OpenRouter API Key |

---

## 数据库位置

所有数据存储在本地：

```
~/.superlocalmemory/memory.db    # SQLite 数据库
~/.superlocalmemory/config.json  # 配置文件
~/.superlocalmemory/backups/     # 自动备份
```

使用自定义路径：

```bash
export SLM_DATA_DIR="/path/to/your/data"
```

---

## 多机 Mesh 组网

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SLM_MESH_PEER_URL` | 不设置 | 远程 MSLM 实例的完整 URL（如 `http://192.168.1.100:8765`） |
| `SLM_MESH_SHARED_SECRET` | 不设置 | 共享 Bearer Token — 两台机器配置相同。当 `SLM_MESH_HOST` 不是 localhost 时必需。 |
| `SLM_MESH_HOST` | `127.0.0.1` | 本机 Mesh 监听 IP |
| `SLM_MESH_WS_PORT` | `7900` | mDNS 服务通告端口 |
| `SLM_MESH_DISCOVERY` | `on` | 设为 `off` 禁用 mDNS 自动发现 |

详见 [SLM 多机配置](slm/multi-machine.md)。

---

## 参考

- [快速上手指南](getting-started-zh.md) — 从安装到日常使用
- [多层次结构记忆](multi-scope-memory-zh.md) — scope_weights 等配置详解
- [SLM 技术文档](slm/INDEX.md) — 上游技术参考

---

*MSLM (Multi-Scope Local Memory) — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
