<p align="center">
  <h1 align="center">MSLM</h1>
  <p align="center"><strong>Multi-Scope Local Memory</strong><br/><em>让 AI 不再遗忘的多层次本地记忆系统</em></p>
  <p align="center"><code>v4.0.0</code> — 为 Claude Code、Cursor、Hermes Agent 等 MCP 兼容 AI 客户端提供持久化记忆。</p>
</p>

<p align="center">
  <code>三层作用域</code> &nbsp;·&nbsp; <code>纯本地运行</code> &nbsp;·&nbsp; <code>MCP 原生</code> &nbsp;·&nbsp; <code>数学驱动检索</code>
</p>

<p align="center">
  <a href="https://pypi.org/project/mslm-memory/"><img src="https://img.shields.io/badge/PyPI-mslm--memory-blue?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"/></a>
  <a href="https://www.npmjs.com/package/mslm-memory"><img src="https://img.shields.io/badge/npm-mslm--memory-red?style=for-the-badge&logo=npm&logoColor=white" alt="npm"/></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=for-the-badge" alt="AGPL v3"/></a>
  <a href="#eu-ai-act-合规"><img src="https://img.shields.io/badge/EU_AI_Act-Design_Compliant-brightgreen?style=for-the-badge" alt="EU AI Act Design Compliant"/></a>
</p>

---

## 为什么选择 MSLM？

每个云端 AI 记忆平台都会将你的数据发送到云端 LLM。**2026 年 8 月 2 日** EU AI Act 生效后，这些云端路径都将面临合规问题。

MSLM 采用完全不同的策略：**用数学替代云端算力。** 基于 SuperLocalMemory 引擎的微分几何、代数拓扑和随机分析技术，在纯 CPU 上实现高质量的本地记忆检索——无需 Docker、无需图数据库、无需 API Key。

**MSLM 的核心优势**：在 SLM 引擎基础上增加了**多层次作用域**（personal / shared / global），让多个 AI Agent 既能保持独立记忆，又能共享团队知识。

### 三层作用域记忆

| 作用域 | 可见范围 | 适用场景 |
|--------|---------|---------|
| **Personal** | 仅自己 | 个人偏好、私密信息 |
| **Shared** | 指定 Agent | 团队协作、领域共享 |
| **Global** | 所有 Agent | 通用知识、技术实体 |

检索时自动并行查询三层作用域，通过 RRF 加权融合返回最佳结果。

---

## 快速安装

```bash
pip install mslm-memory
```

> MSLM 也以 `superlocalmemory` 包名发布，`pip install superlocalmemory` 效果相同。

```bash
mslm setup          # 交互式初始化向导
mslm serve start    # 启动后台守护进程
```

在 Hermes Agent 中接入：

```bash
hermes mcp add mslm --command mslm --args mcp
```

---

## 核心特性

### 🧠 多层次记忆架构
- **三层作用域**：personal / shared / global，灵活控制记忆可见性
- **全局权威实体**：React、Python 等技术实体所有 Agent 共享，避免重复
- **域标签自动匹配**：48 个内置实体→领域映射，跨 Agent 自动共享

### 🔬 数学驱动检索
- **7 通道混合检索**：语义向量 + BM25 关键词 + 实体图谱 + 时间感知 + Hopfield 联想 + Profile 过滤 + 图传播
- **RRF 加权融合**：跨 scope 结果智能排序
- **Fisher-Rao 几何距离**：信息几何相似度评分
- **Sheaf 一致性检测**：矛盾记忆自动发现
- **Langevin 生命周期**：记忆自动强化/衰减

### 🏠 完全本地化
- **零云端依赖**：所有数据存储在本地 SQLite
- **CPU 即可运行**：无需 GPU，无需 Docker
- **EU AI Act 合规**：数据永不离开你的机器

### 🔌 MCP 原生集成
- **33 个核心工具**：记忆存储、语义检索、实体管理、会话追踪
- **Hermes Agent 深度整合**：一键注册，自然语言调用
- **Claude Code / Cursor / Windsurf**：所有 MCP 兼容客户端即装即用

### 📊 Web 仪表盘
- 记忆网络图可视化
- 检索统计与性能监控
- 系统健康检查（Fisher-Rao / Sheaf / Langevin）
- 记忆维护操作

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
| [技术参考](docs/slm/INDEX.md) | 上游 SLM 架构与 API 文档 |

---

## 与 SuperLocalMemory 的关系

MSLM (Multi-Scope Local Memory) 是 SuperLocalMemory 的独立发行版，专注于多层次记忆协作场景：

- **共享同一引擎**：核心检索、存储、数学层完全基于 SLM
- **增加协作层**：三层作用域、全局实体、跨 Agent 知识共享
- **独立品牌**：MSLM 专注团队协作场景，SLM 专注单用户记忆

核心记忆引擎由 [SuperLocalMemory](https://github.com/qualixar/superlocalmemory) 驱动（AGPL-3.0-or-later）。

---

## 社区

- **问题反馈**：[GitHub Issues](https://github.com/kenyonxu/superlocalmemory/issues)
- **上游项目**：[SuperLocalMemory](https://github.com/qualixar/superlocalmemory)
- **许可证**：AGPL-3.0-or-later

---

*MSLM — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
