<p align="center">
  <img src="assets/branding/mslm-banner.png" alt="MSLM — Multi-Scope Local Memory：让 AI 不再遗忘的多层次本地记忆系统" width="100%"/>
</p>

<h1 align="center">MSLM</h1>

<p align="center"><strong>Multi-Scope Local Memory</strong><br/>
<em>让 AI 不再遗忘的多层次本地记忆系统</em></p>

<p align="center"><code>v4.2.0</code> · 内核已合并上游 SuperLocalMemory 4.1.11<br/>
为 Claude Code、Cursor、Hermes Agent 等 MCP 兼容客户端提供持久化记忆</p>

<p align="center">
  <a href="https://pypi.org/project/mslm-memory/"><img src="https://img.shields.io/badge/PyPI-mslm--memory-blue?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"/></a>
  <a href="https://www.npmjs.com/package/mslm-memory"><img src="https://img.shields.io/badge/npm-mslm--memory-red?style=for-the-badge&logo=npm&logoColor=white" alt="npm"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=for-the-badge" alt="AGPL v3"/></a>
</p>

<p align="center">📖 <strong>文档</strong>：<strong>中文（本页）</strong> · <a href="README-en.md">English</a> · <a href="docs/INDEX-zh.md">文档索引</a> · <a href="docs/hermes-agent-guide-zh.md">Hermes 集成指南</a> · <a href="CHANGELOG.md">更新日志</a></p>

<p align="center"><code>三层作用域</code> &nbsp;·&nbsp; <code>纯本地运行</code> &nbsp;·&nbsp; <code>MCP 原生</code> &nbsp;·&nbsp; <code>数学驱动检索</code></p>

---

MSLM（Multi-Scope Local Memory）是一个本地优先的 AI Agent 多层次记忆系统，基于 [SuperLocalMemory](https://github.com/qualixar/superlocalmemory) 引擎构建。无需 Docker、无需图数据库、无需 API Key。

## 为什么选择 MSLM？

云端 AI 记忆平台需要把你的数据发送到云端 LLM；EU AI Act 已于 2026 年 8 月生效，这些云端路径正面临越来越大的合规压力。

MSLM 采用完全不同的策略：**用数学替代云端算力**。基于 SuperLocalMemory 引擎的微分几何、代数拓扑和随机分析技术，在纯 CPU 上实现高质量的本地记忆检索。

**MSLM 在引擎之上增加了多层次协作层**：三层作用域（personal / shared / global）让多个 AI Agent 既能保持独立记忆，又能共享团队知识——这是 MSLM（多 Agent 协作记忆）与上游 SuperLocalMemory（单用户记忆）的分界线。

## 三层作用域记忆

| 作用域 | 可见范围 | 适用场景 |
|--------|---------|---------|
| **personal** | 仅当前 profile | 个人偏好、私密信息 |
| **shared** | 指定 Agent / profile | 团队协作、领域共享 |
| **global** | 所有 Agent | 通用知识、技术实体 |

- 检索时自动并行查询三层作用域，通过 RRF 加权融合返回最佳结果；跨作用域读取默认关闭（default-deny），需显式开启。
- **全局权威实体**：React、Python 等技术实体由所有 Agent 共享，避免各 Agent 重复构建。
- **按请求 profile 路由（v4.2.0 新增）**：`remember` / `recall` / `list_recent` 可携带可选的 `profile_id`，操作直接路由到对应命名空间，不产生任何全局指针副作用；不传则与旧版本行为完全一致。Hermes MemoryProvider 默认锁定到其配置的 profile（`SLM_HERMES_PIN_PROFILE=0` 可改回跟随活动指针）。

## 核心特性

### 🧠 多层次记忆架构

- **三层作用域**：personal / shared / global，灵活控制记忆可见性
- **全局权威实体**：技术实体跨 Agent 共享，避免重复
- **域标签自动匹配**：48 个内置实体→领域映射，跨 Agent 自动共享

### 🔬 数学驱动检索

- **7 通道混合检索**：语义向量 + BM25 关键词 + 实体图谱 + 时间感知 + Hopfield 联想 + Profile 过滤 + 图传播
- **RRF 加权融合**：跨作用域结果智能排序
- **Fisher-Rao 信息几何**相似度 · **Sheaf 一致性检测**（矛盾记忆自动发现）· **Langevin 生命周期**（记忆自动强化/衰减）

### 🧩 Hermes Agent MemoryProvider（推荐）

- **原生集成**：无需 MCP 子进程，零额外延迟，Hermes 启动自动加载
- **自动上下文注入**：每轮自动预取相关记忆，自动持久化对话事实
- **三层作用域原生支持**：`slm_recall` / `slm_remember` / `slm_status` 完整支持
- **即配即用**：`hermes memory setup` 选择 superlocalmemory，或一段 YAML 配置

### 🔌 通用 MCP 集成

- **stdio / HTTP 双传输**：工具面可通过 `SLM_MCP_PROFILE` 分档（core / full / power 等）
- **Claude Code / Cursor / Windsurf** 等所有 MCP 兼容客户端即装即用

### 🏠 完全本地化

- **零云端依赖**：所有数据存储在本地 SQLite（WAL 模式）
- **CPU 即可运行**：无需 GPU、无需 Docker
- **Mode A 下记忆内容不出本机**

### 📊 Web 仪表盘

`slm dashboard` 打开本地运维视图：记忆网络图、实体时间线、检索统计、系统健康检查（Fisher-Rao / Sheaf / Langevin）、记忆维护操作。

---

## 快速开始

### 安装

```bash
# pip（Python 3.11+，提供 slm 命令）
python -m pip install mslm-memory
slm setup          # 交互式初始化向导（推荐 Mode A）
slm doctor         # 环境自检

# 或 npm（Node 18+，额外提供 mslm 等价命令）
npm install -g mslm-memory
mslm setup && mslm doctor
```

> MSLM 也以 `superlocalmemory` 包名发布，`pip install superlocalmemory` 效果相同。

### 首次使用

```bash
slm remember "Alice 在 Google 担任 Staff Engineer" --json
slm recall "Alice 在做什么工作？"
slm status
```

### 接入 Hermes Agent（推荐 MemoryProvider 方式）

```bash
hermes memory setup
# 选择 superlocalmemory，按提示配置（默认全本地 Mode A 即可）
```

或直接编辑 `~/.hermes/config.yaml`：

```yaml
memory:
  provider: superlocalmemory
  superlocalmemory:
    mode: "A"             # A 完全本地 | B 本地 Ollama | C 云端 LLM
    include_global: true  # 检索时包含跨 profile 共享的事实
```

### 接入其他 MCP 客户端

```json
{
  "mcpServers": {
    "mslm": { "command": "slm", "args": ["mcp"] }
  }
}
```

HTTP 传输（需先 `slm serve start` 启动守护进程）：`http://127.0.0.1:8765/mcp/`

---

## 运行模式

| 模式 | 名称 | 说明 |
|------|------|------|
| **A** | Local Guardian | 零云端、零 LLM，纯本地处理（默认，推荐） |
| **B** | Smart Local | 本地 Ollama LLM 增强 |
| **C** | Full Power | 云端 LLM 辅助（内容发送至配置的 Provider） |

```bash
slm mode a   # 切换运行模式
```

---

## 与 SuperLocalMemory 的关系

MSLM 是 [SuperLocalMemory](https://github.com/qualixar/superlocalmemory) 的独立发行版（fork），当前内核已合并上游 4.1.11：

- **共享同一引擎**：存储、7 通道检索、数学层完全基于上游 SLM
- **MSLM 增加协作层**：三层作用域、全局权威实体、跨 Agent 知识共享、Hermes MemoryProvider、按请求 profile 路由
- **上游能力同样可用**：SLM-Mesh、企业 RBAC、Cache/Compress 等，见[上游文档归档](docs/slm/INDEX.md)
- **独立品牌**：MSLM 专注多 Agent 协作场景，SLM 专注单用户记忆

---

## 文档导航

| 文档 | 说明 |
|------|------|
| [快速上手指南](docs/getting-started-zh.md) | 从安装到日常使用的完整指引 |
| [多层次结构记忆](docs/multi-scope-memory-zh.md) | 三层作用域核心概念与使用 |
| [Hermes Agent 集成](docs/hermes-agent-guide-zh.md) | MCP 协议集成指南 |
| [配置指南](docs/configuration-zh.md) | 运行模式、Provider、环境变量 |
| [记忆导入指南](docs/memory-import-guide-zh.md) | 从外部系统批量导入记忆 |
| [文档索引](docs/INDEX-zh.md) | 全部文档导航 |
| [上游 SLM 技术文档](docs/slm/INDEX.md) | 上游架构与 API 文档 |

---

## 社区与许可证

- **问题反馈**：[GitHub Issues](https://github.com/kenyonxu/superlocalmemory/issues)
- **上游项目**：[SuperLocalMemory](https://github.com/qualixar/superlocalmemory)
- **许可证**：AGPL-3.0-or-later（见 [LICENSE](LICENSE)、[ATTRIBUTION.md](ATTRIBUTION.md)）

核心记忆引擎由 [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)（Qualixar / Varun Pratap Bhardwaj）提供，在此致谢。

---

*MSLM — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
