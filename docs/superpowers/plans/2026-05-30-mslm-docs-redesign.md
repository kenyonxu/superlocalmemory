# MSLM 文档重构实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 SuperLocalMemory 文档重构为 MSLM (Multi-Scope Local Memory) 独立品牌，按三层策略（品牌层重写、运维层修订、技术层归档）重组所有文档，并实现中英双语版本。

**Architecture:** 三层文档策略 — 🔴 品牌层以 MSLM 身份全面重写（README, getting-started, multi-scope-memory, INDEX）；🟡 运维层保留内容仅修订品牌引用（hermes-agent-guide, configuration, memory-import-guide）；⚪ 技术层原样归档到 `docs/slm/` 子目录。品牌层和运维层均提供中英双语版本（`-zh.md` / `-en.md`），顶部放置双语切换链接。

**Tech Stack:** Markdown only — 纯文档任务，不涉及代码变更。

---

## Chunk 1: 归档技术文档到 docs/slm/

### Task 1.1: 创建 docs/slm/ 目录结构

**Files:**
- Create: `docs/slm/INDEX.md`

- [ ] **Step 1: 创建 docs/slm/ 目录**

```bash
mkdir -p /home/kai-remote/github/superlocalmemory/docs/slm
```

- [ ] **Step 2: 移动所有技术文档到 docs/slm/**

被移动的文件清单（全部原样保留，不修改内容）：

核心技术文档：
- `docs/ARCHITECTURE.md` → `docs/slm/ARCHITECTURE.md`
- `docs/api-reference.md` → `docs/slm/api-reference.md`
- `docs/cli-reference.md` → `docs/slm/cli-reference.md`
- `docs/mcp-tools.md` → `docs/slm/mcp-tools.md`
- `docs/compliance.md` → `docs/slm/compliance.md`
- `docs/troubleshooting.md` → `docs/slm/troubleshooting.md`
- `docs/skill-evolution.md` → `docs/slm/skill-evolution.md`
- `docs/upstream-contribution-strategy.md` → `docs/slm/upstream-contribution-strategy.md`

其他技术/参考文档：
- `docs/auto-memory.md` → `docs/slm/auto-memory.md`
- `docs/cloud-backup.md` → `docs/slm/cloud-backup.md`
- `docs/DASHBOARD-COVERAGE.md` → `docs/slm/DASHBOARD-COVERAGE.md`
- `docs/errors.md` → `docs/slm/errors.md`
- `docs/ide-setup.md` → `docs/slm/ide-setup.md`
- `docs/migration-from-v2.md` → `docs/slm/migration-from-v2.md`
- `docs/multi-machine.md` → `docs/slm/multi-machine.md`
- `docs/profiles.md` → `docs/slm/profiles.md`

诊断/历史记录（归档但不列为主要文档）：
- `docs/hermes-agent-slmd-busy-diagnosis-2026-05-15.md` → `docs/slm/hermes-agent-slmd-busy-diagnosis-2026-05-15.md`
- `docs/hermes-agent-slmd-fix-record-2026-05-15.md` → `docs/slm/hermes-agent-slmd-fix-record-2026-05-15.md`

子目录：
- `docs/benchmarks/` → `docs/slm/benchmarks/`
- `docs/screenshots/` → `docs/slm/screenshots/`
- `docs/v2-archive/` → `docs/slm/v2-archive/`

```bash
cd /home/kai-remote/github/superlocalmemory/docs

# 核心技术文档
mv ARCHITECTURE.md slm/
mv api-reference.md slm/
mv cli-reference.md slm/
mv mcp-tools.md slm/
mv compliance.md slm/
mv troubleshooting.md slm/
mv skill-evolution.md slm/
mv upstream-contribution-strategy.md slm/

# 其他参考文档
mv auto-memory.md slm/
mv cloud-backup.md slm/
mv DASHBOARD-COVERAGE.md slm/
mv errors.md slm/
mv ide-setup.md slm/
mv migration-from-v2.md slm/
mv multi-machine.md slm/
mv profiles.md slm/

# 诊断记录
mv hermes-agent-slmd-busy-diagnosis-2026-05-15.md slm/
mv hermes-agent-slmd-fix-record-2026-05-15.md slm/

# 子目录
mv benchmarks/ slm/
mv screenshots/ slm/
mv v2-archive/ slm/
```

- [ ] **Step 3: 验证移动后的文件结构**

```bash
ls -la /home/kai-remote/github/superlocalmemory/docs/slm/
ls /home/kai-remote/github/superlocalmemory/docs/
```

预期结果：`docs/slm/` 下有 ~25 个文件/目录，`docs/` 下只剩下：
- `getting-started.md`（待重写）
- `multi-scope-memory-roadmap.md`（待重写）
- `hermes-agent-guide.md`（待修订）
- `configuration.md`（待修订）
- `memory-import-guide.md`（待修订）
- `slm/`（刚创建的归档目录）
- `superpowers/`（保留）

- [ ] **Step 4: 创建 docs/slm/INDEX.md 上游文档索引**

内容概要：
```markdown
# SLM 上游文档索引

本目录包含 SuperLocalMemory (SLM) 上游原装技术文档，作为 MSLM 的技术参考层。
MSLM 基于 SLM 构建，使用相同的底层引擎和协议。

## 核心架构
- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构设计
- [api-reference.md](api-reference.md) — API 参考
- [cli-reference.md](cli-reference.md) — CLI 命令参考
- [mcp-tools.md](mcp-tools.md) — MCP 工具完整参考

## 运维与配置
- [compliance.md](compliance.md) — 合规性（EU AI Act, GDPR）
- [troubleshooting.md](troubleshooting.md) — 故障排查
- [configuration.md 技术参考] — 见上层 docs/configuration-zh.md

## 高级功能
- [skill-evolution.md](skill-evolution.md) — 技能演化引擎
- [auto-memory.md](auto-memory.md) — 自动记忆
- [multi-machine.md](multi-machine.md) — 多机 Mesh 组网

## 历史与迁移
- [migration-from-v2.md](migration-from-v2.md) — V2 迁移指南
- [profiles.md](profiles.md) — 用户画像
- [upstream-contribution-strategy.md](upstream-contribution-strategy.md) — 上游贡献策略
```

- [ ] **Step 5: 提交**

```bash
cd /home/kai-remote/github/superlocalmemory
git add docs/slm/
git add docs/
git commit -m "docs: archive upstream technical docs to docs/slm/"
```

---

## Chunk 2: 创建 docs/INDEX.md — MSLM 文档索引主页

### Task 2.1: 创建 docs/INDEX-zh.md（中文）

**Files:**
- Create: `docs/INDEX-zh.md`

内容要点：
- MSLM 品牌标识和一句话介绍
- 分类导航：🚀 快速上手 / 🧠 核心概念 / ⚙️ 运维配置 / 📚 技术参考（链接到 slm/）
- 底部 "powered by SLM" 标注

### Task 2.2: 创建 docs/INDEX-en.md（英文）

**Files:**
- Create: `docs/INDEX-en.md`

内容要点：INDEX-zh.md 的英文对应版本，顶部双语切换链接。

- [ ] **Step 1: 编写 docs/INDEX-zh.md**
- [ ] **Step 2: 编写 docs/INDEX-en.md**
- [ ] **Step 3: 验证两个文件的顶级标题和切换链接互相正确指向**
- [ ] **Step 4: 提交**

```bash
git add docs/INDEX-zh.md docs/INDEX-en.md
git commit -m "docs: add MSLM docs index (bilingual)"
```

---

## Chunk 3: 重写 getting-started（🔴 品牌层）

### Task 3.1: 创建 getting-started-zh.md（中文）

**Files:**
- Create: `docs/getting-started-zh.md`
- Remove: `docs/getting-started.md`（原文件，内容已迁移到双语版本中）

MSLM 品牌化重写要点：
- 标题改为 "MSLM 快速上手指南"
- 安装命令改为 MSLM 包名（`pip install mslm` 作为主推，标注 "或 pip install superlocalmemory"）
- CLI 命令改为 `mslm`（标注 "`slm` 别名也可用"）
- 守护进程、MCP 接入、代理配置、数据目录管理等内容保留
- 添加与上游 SLM 的关系说明："MSLM 基于 SuperLocalMemory 构建"
- Hermes Agent 集成说明改为以 MSLM 视角叙述
- 底部标注 "powered by SuperLocalMemory"

### Task 3.2: 创建 getting-started-en.md（英文）

**Files:**
- Create: `docs/getting-started-en.md`

英文对应版本，内容一致。

- [ ] **Step 1: 编写 docs/getting-started-zh.md**
- [ ] **Step 2: 编写 docs/getting-started-en.md**
- [ ] **Step 3: 删除旧文件**

```bash
git rm docs/getting-started.md
```

- [ ] **Step 4: 验证双语链接正确**
- [ ] **Step 5: 提交**

```bash
git add docs/getting-started-zh.md docs/getting-started-en.md
git commit -m "docs: rewrite getting-started as MSLM brand (bilingual)"
```

---

## Chunk 4: 重写 multi-scope-memory（🔴 品牌层）

### Task 4.1: 创建 multi-scope-memory-zh.md（中文）

**Files:**
- Create: `docs/multi-scope-memory-zh.md`
- Remove: `docs/multi-scope-memory-roadmap.md`

MSLM 品牌化重写要点：
- 从"架构设计文档"转变为"MSLM 核心功能介绍"
- 标题改为 "MSLM 多层次结构记忆"
- 将 7 节架构分析压缩为 3 节用户导向内容：
  1. 三层作用域模型（personal/group/global → personal/shared/global）
  2. 跨作用域检索与 RRF 融合
  3. 全局权威实体与知识共享
- 删除代码级改动细节（schema 变更、DatabaseManager 改动等）
- 删除 Phase 1-4 实施路线图
- 改为以用户视角说明使用方法（CLI 命令、MCP 工具参数）
- 保留全局实体概念、域标签、多 Agent 协作示例
- 底部标注 "powered by SLM"

### Task 4.2: 创建 multi-scope-memory-en.md（英文）

**Files:**
- Create: `docs/multi-scope-memory-en.md`

英文对应版本。

- [ ] **Step 1: 编写 docs/multi-scope-memory-zh.md**
- [ ] **Step 2: 编写 docs/multi-scope-memory-en.md**
- [ ] **Step 3: 删除旧文件**

```bash
git rm docs/multi-scope-memory-roadmap.md
```

- [ ] **Step 4: 验证双语链接正确**
- [ ] **Step 5: 提交**

```bash
git add docs/multi-scope-memory-zh.md docs/multi-scope-memory-en.md
git commit -m "docs: rewrite multi-scope-memory as MSLM brand (bilingual)"
```

---

## Chunk 5: 修订运维层文档（🟡 运维层）

### Task 5.1: 修订 hermes-agent-guide（中英双语）

**Files:**
- Create: `docs/hermes-agent-guide-zh.md`（基于原 `hermes-agent-guide.md` 内容）
- Create: `docs/hermes-agent-guide-en.md`
- Remove: `docs/hermes-agent-guide.md`

修订要点（内容保留，品牌引用更新）：
- 标题改为 "MSLM × Hermes Agent 集成指南" / "MSLM × Hermes Agent Integration Guide"
- 包名改为 MSLM，命令改为 `mslm`
- 底部改为 "powered by SuperLocalMemory"
- 移除 "Part of Qualixar" 引用
- 移除 `superlocalmemory.com` 链接
- 其他技术内容（MCP 配置、三层作用域、CLI 速查）原样保留

### Task 5.2: 修订 configuration（中英双语）

**Files:**
- Create: `docs/configuration-zh.md`
- Create: `docs/configuration-en.md`（基于原 `configuration.md` 内容）
- Remove: `docs/configuration.md`

修订要点：
- 标题改为 "MSLM 配置指南" / "MSLM Configuration"
- 命令改为 `mslm`
- 数据目录改为 `~/.mslm/` 或保留 `~/.superlocalmemory/`（需要决策）
- 移除 Copyright/Qualixar 引用
- 其他技术内容（三种模式、Provider、环境变量等）原样保留

### Task 5.3: 修订 memory-import-guide（中英双语）

**Files:**
- Create: `docs/memory-import-guide-zh.md`（基于原 `memory-import-guide.md` 内容）
- Create: `docs/memory-import-guide-en.md`
- Remove: `docs/memory-import-guide.md`

修订要点：
- 标题改为 "MSLM 记忆导入指南" / "MSLM Memory Import Guide"
- CLI 命令改为 `mslm`
- Python 导入路径保持 `from superlocalmemory.core.engine import MemoryEngine`（引擎层不改名）
- 底部标注 "powered by SuperLocalMemory"
- 其他技术内容原样保留

- [ ] **Step 1: 编写 hermes-agent-guide-zh.md + hermes-agent-guide-en.md**
- [ ] **Step 2: 编写 configuration-zh.md + configuration-en.md**
- [ ] **Step 3: 编写 memory-import-guide-zh.md + memory-import-guide-en.md**
- [ ] **Step 4: 删除旧文件**

```bash
git rm docs/hermes-agent-guide.md docs/configuration.md docs/memory-import-guide.md
```

- [ ] **Step 5: 验证所有双语链接正确**
- [ ] **Step 6: 提交**

```bash
git add docs/hermes-agent-guide-zh.md docs/hermes-agent-guide-en.md \
        docs/configuration-zh.md docs/configuration-en.md \
        docs/memory-import-guide-zh.md docs/memory-import-guide-en.md
git commit -m "docs: revise ops-layer docs for MSLM brand (bilingual)"
```

---

## Chunk 6: 更新根 README.md（MSLM 品牌化 + 双语）

### Task 6.1: 创建 README-zh.md + README-en.md

**Files:**
- Create: `README-zh.md`
- Create: `README-en.md`
- Remove: `README.md`（原 724 行英文 README）

MSLM 品牌化写入要点：

**README-zh.md（中文首页）**：
- 标题: "MSLM — Multi-Scope Local Memory"
- 副标题: "让 AI 不再遗忘的多层次本地记忆系统"
- 核心卖点：多层次作用域（personal/shared/global）、纯本地运行、MCP 原生、数学驱动
- 快速安装: `pip install mslm`
- 功能亮点：三层作用域记忆、7 通道混合检索、全局权威实体、自动生命周期管理
- 与 SLM 的关系：底部 "powered by SuperLocalMemory" + 链接到上游
- 文档导航：链接到 docs/INDEX-zh.md

**README-en.md（英文）**：
- README-zh.md 的英文对应版本

- [ ] **Step 1: 编写 README-zh.md**
- [ ] **Step 2: 编写 README-en.md**
- [ ] **Step 3: 创建符号链接或 GitHub Pages 配置使 README-zh.md 成为默认首页**

由于 GitHub 默认显示 README.md，需要保留一个 README.md 作为跳转或使用 GitHub 的默认分支设置。
推荐方案：`README.md` 作为简短跳转页，指向 `README-zh.md` 和 `README-en.md`。

```markdown
# MSLM — Multi-Scope Local Memory

[中文文档](README-zh.md) | [English Docs](README-en.md)
```

- [ ] **Step 4: 删除旧 README.md 内容，替换为跳转页或直接使用 README-zh.md 内容**
- [ ] **Step 5: 提交**

```bash
git add README-zh.md README-en.md README.md
git commit -m "docs: rebrand root README as MSLM (bilingual)"
```

---

## 验证清单

全部 Chunk 完成后运行：

- [ ] 确认 `docs/` 目录结构符合设计方案
- [ ] 确认所有品牌层文档顶部有双语切换链接且互相正确指向
- [ ] 确认所有运维层文档顶部有双语切换链接且互相正确指向
- [ ] 确认 `docs/slm/` 下有上游文档索引 INDEX.md
- [ ] 确认根目录有 README-zh.md + README-en.md
- [ ] 确认所有 MSLM 品牌文档底部有 "powered by SuperLocalMemory" 标注
- [ ] 确认没有遗留的陈旧的品牌引用（Qualixar、superlocalmemory.com）
- [ ] 用 `git status` 确认没有遗漏的文件
