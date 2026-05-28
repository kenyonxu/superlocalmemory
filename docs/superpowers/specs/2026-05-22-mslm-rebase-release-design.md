# MSLM Rebase & Independent Release Design

> 2026-05-22 — 将 SuperLocalMemory fork rebase 到上游 v3.4.56，建立独立发布体系，定义追上游工作流

## 背景

- **上游**: `qualixar/superlocalmemory`，持续活跃开发
- **Fork**: `kenyonxu/superlocalmemory`，101 commits 领先，实现了 multi-scope memory (personal/global/shared)
- **上游方向**: 性能 + 可靠性基建（并行通道、FSRS 衰减、热记忆、冷启动修复），与我们的 scope 工作互补
- **上游已接受我们的 2 个 bug fix**（WAL busy_timeout、logger.exception），证明贡献方向一致

## 目标

1. 将 fork rebase 到上游 v3.4.56，获取并行通道和 FSRS 衰减
2. 建立 `mslm` 独立品牌的发布体系
3. 定义标准的追上游工作流

---

## 一、Git 仓库改造

### 分支模型

```
main      → 干净历史（6 squash 提交 + upstream/main），tag 发布，对外
develop   → 完整 101 提交历史 + 未来开发，日常开发 + 追上游
```

### Step 1: 保留完整历史到 develop

```bash
git branch develop main          # 快照当前 main 为 develop
git checkout develop
git merge upstream/main          # 合并 upstream v3.4.56，解决冲突
pytest tests/ -q --tb=short      # 验证测试全过
```

### Step 2: 构建干净的 main

```bash
git checkout --orphan new-main upstream/main
# 从 develop 逐个 cherry-pick squash 后的 6 个分组
```

**main 分支的 6 个提交（从 develop squash-cherry-pick）：**

```
v3.4.56 (upstream/main)
  │
  ├── feat(multiscope): core infrastructure
  │     scope/shared_with 列，scope-aware _scope_where()，
  │     engine/pipeline/7channels scope 参数，MCP scope 工具，
  │     M014 migration，ScopeWeights config，cross-agent global recall fix
  │
  ├── feat(multiscope): domain tags
  │     domain_mapping 表，M015 migration，skill_tags 属性和传播，
  │     LLM 分类器，add_domain_mapping/remove_domain_mapping MCP 工具
  │
  ├── feat(multiscope): global authoritative entities
  │     Phase 3 global-first entity resolution，cross-scope alias/fuzzy
  │
  ├── feat(multiscope): scope-e2e wiring
  │     CLI/Dashboard/materializer scope 传递，entity merge CLI/MCP，
  │     scope-r2 recall deferral
  │
  ├── fix: daemon reliability
  │     /health engine recovery，HealthMonitor zombie reaper，
  │     entity/embedding backfill，materializer entity extraction，
  │     5s get_engine failure cooldown
  │
  └── fix: proxy and environment hardening
         ProxyConfig，embedding worker proxy env vars，
         HF_ENDPOINT removal
```

### 冲突文件预估

| 文件 | 冲突程度 | 原因 |
|------|---------|------|
| `engine.py` | 中 | 上游：并行通道/FSRS；我们：scope 参数/backfill |
| `recall_pipeline.py` | 中 | 上游：FSRS 衰减；我们：scope 过滤 |
| `unified_daemon.py` | 中 | 上游：pre-warm/JSON清理；我们：materializer/health |
| `engine_wiring.py` | 低 | 上游：Ollama 优先级；我们：ScopeWeights/ProxyConfig |
| `retrieval/engine.py` | 低 | 上游：性能调优；我们：scope 权重/RRF |
| `spreading_activation.py` | 低 | 上游：fan-out缩减；我们：scope 感知 |
| `tools_active.py` | 低 | 上游：emergency FTS5；我们：scope |
| `commands.py` | 低 | 上游：mode切换；我们：entity/scope CLI |
| 其余文件 | 极低 | 不同函数/区域，自动合并 |

---

## 二、包发布体系

### 对外名字

| 项目 | 值 |
|------|-----|
| PyPI 包名 | `mslm` |
| npm 包名 | `mslm` |
| CLI 命令 | `mslm`（`slm` 保留为别名） |
| 版本号起始 | `v4.0.0` |

### 内部兼容 — 最小改动

**Python 包目录、数据路径、环境变量全部不变：**

```
Python 包目录:  src/superlocalmemory/   ← 不变
数据目录:       ~/.superlocalmemory/    ← 不变
环境变量:       SLM_* 前缀               ← 不变
配置文件:       config.json             ← 不变
数据库:         memory.db               ← 不变
```

### PyPI

```
安装:     pip install mslm
CLI:      mslm status
导入:     保持 import superlocalmemory（内部不变）
```

### npm

```
安装:     npm install -g mslm
使用:     npx mslm recall "xxx"
实现:     thin wrapper，自动调用 pip install mslm
```

### 版本号规则

```
v4.0.0    → 首次独立发布
v4.0.x    → 我们的 bug fix
v4.x.0    → 我们的新功能
v4.x.y    → 追上游 rebase + 可能的修复
```

---

## 三、追上游工作流

### 节奏

每 2-4 周，或上游发布重要性能/可靠性版本时。

### 标准步骤

```bash
# 1. 拉取上游
git fetch upstream

# 2. rebase develop
git checkout develop
git rebase upstream/main       # 解决冲突，优先保留上游基建代码

# 3. 验证
pytest tests/ -q --tb=short
slm health                     # 功能烟雾测试

# 4. squash 新增变更到 main
#    将 develop 上 rebase 后产生的新提交按主题 squash 到 main 的对应提交

# 5. 打 tag，推送
git tag v4.x.0
git push origin main develop --tags
```

### 冲突解决策略

- **上游基建代码优先**（并行通道、FSRS 公式、keep_alive 等）
- **我们的 scope 增量代码**合并到对应函数的正确位置
- 如遇结构性重写（上游重构了整段我们修改过的代码），先理解上游意图，再以 scope 兼容方式写入

### 记录

每次追上游后，在 `docs/upstream-contribution-strategy.md` 末尾追加一行记录：

```
- 2026-05-22: rebase to v3.4.56, conflicts in engine/recall/daemon resolved
```

---

## 四、本机迁移

已有安装无需任何迁移动作：

```bash
cd ~/github/superlocalmemory
git checkout main              # 切到新 main 分支
pip install -e .               # 重新注册，pyproject.toml 中 name 已改为 mslm
mslm status                    # 新 CLI
slm status                     # 别名仍可用
```

数据目录 `~/.superlocalmemory/` 原封不动，数据库、配置、profile 全部无缝继承。

---

## 五、不做的事情

- **不修改内部 Python 包名**（保持 `import superlocalmemory`）
- **不迁移数据路径**（保持 `~/.superlocalmemory/`）
- **不修改环境变量前缀**（保持 `SLM_*`）
- **不修改上游 LICENSE**（AGPL-3.0-or-later，保留原始版权声明）
