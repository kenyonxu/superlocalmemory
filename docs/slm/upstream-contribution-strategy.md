# 上游贡献与公布策略

> 2026-05-17 初稿 → 2026-06-02 上游回复 → 2026-06-15 更新策略

## 当前状态

- **上游**: `qualixar/superlocalmemory`，持续活跃开发
- **Fork**: `kenyonxu/superlocalmemory`（MSLM 发行版）
- **分歧点**: `19f051c` (v3.4.45, 2026-05-13) — 已 rebase 至上游最新
- **Fork 新增**: ~89 commits，含 multi-scope memory (Phase 1+2+3+2B) + scope-e2e + MSLM 品牌化文档 + 多项修复
- **上游 RFC Issue**: [#20](https://github.com/qualixar/superlocalmemory/issues/20) — **Varun 于 2026-06-02 积极回复，确认 multi-scope memory 在路线图上**
- **Bug fix PR**: [#24](https://github.com/qualixar/superlocalmemory/pull/24) WAL busy_timeout, [#25](https://github.com/qualixar/superlocalmemory/pull/25) logger.exception — 等待审阅

## 上游回复要点（Varun, 2026-06-02）

1. Multi-scope memory 在他路线图上，认真考虑
2. 要求 PR 按层拆分：Schema → Retrieval → External Interface
3. Schema/migration 部分他会最仔细审阅（涉及现有用户 DB）
4. Phase 2/3（域标签、全局实体解析）要先对齐设计，避免和他正在做的 entity/graph 工作冲突
5. 想直接沟通再开始提交
6. Issue #20 保留为 multi-scope memory 跟踪帖

## 新策略：双轨并行

### 轨道一：上游 Engine 层变更

将 multi-scope memory 的核心引擎变更按三层拆分提交上游：

**PR 1 — Schema + Migration（~300 LOC）**
- 8 个核心表新增 `scope` / `group_id` 列
- 迁移脚本 `M014_add_scope_support.py`（向后兼容，现有数据默认 `personal`）
- Models 更新（15+ dataclass）
- 复合索引
- 纯存储层，无检索或接口变更

**PR 2 — Retrieval Layer（~400 LOC）**
- `recall_pipeline.py`：多 scope 并行检索 + 加权 RRF 融合
- 7 个 channel：各 channel 增加 scope 过滤
- `DatabaseManager`：30+ 查询方法增加可选 scope/group_id 参数
- 权重通过 `config.json` 配置

**PR 3 — External Interface（~300 LOC）**
- MCP 工具参数扩展（remember scope/shared_with，recall include_global/include_shared）
- CLI 参数扩展
- WorkerPool IPC scope 参数传递
- `MemoryEngine` 签名扩展（向后兼容默认值）

### 轨道二：MSLM 发行版

MSLM (`mslm-memory`) 继续作为下游发行版存在，无论上游是否合并：

| 场景 | MSLM 角色 |
|------|----------|
| 上游合并全部 | 薄发行层：品牌 + 文档 + Hermes provider + 中文支持 |
| 上游部分合并 | 携带未合并的增量 + 发行层 |
| 上游不合并 | 携带完整 delta + 发行层 |

MSLM 的核心价值层（独立于引擎变更）：
- 中英双语文档和品牌
- Hermes Agent MemoryProvider 原生集成
- 团队协作场景的 presets 和最佳实践
- PyPI/npm 双包发布

## 已验证的上游原生 Bug

通过对比 `upstream/main` 代码逐一验证，确认仅 2 个修复是针对上游原生代码的：

| 提交 | 问题 | 上游文件 |
|------|------|---------|
| `b8f847f` | WAL busy_timeout 顺序错误 — 先设 `journal_mode=WAL` 再设 `busy_timeout`，WAL 使用默认 5s 超时而非配置的 10s | `storage/database.py:69-70` |
| `deec6e0` | Engine init 失败丢失 traceback — `logger.warning(exc)` 不打印堆栈 | `server/unified_daemon.py:539` |

以下修复**不适用**于上游（均为自有代码或环境的改动）：

- `f75600a` — scope 参数 TypeError（multi-scope 功能参数遗漏）
- `8499653` — backfill 只查 engine profile（backfill 功能设计缺陷）
- `4b4375b` — materializer 跳过 entity 提取（修复非上游代码 `eedd884`）
- `5d8d39f` — BM25 scope / merge_entities / entity listing（全部是 multi-scope 代码）
- `bbe81b4` — 代理环境变量传递（功能增强，非 bug）
- `4ac0d5d` — 移除 HF_ENDPOINT（环境特定问题）

## PR 执行计划

### 第一步：清理基础 ✅

- 2 个 bug fix PR 已提交（#24, #25），等待上游审阅合并

### 第二步：Multi-Scope PR 三部曲

| PR | 内容 | 预计 LOC | 依赖 |
|----|------|---------|------|
| PR-A | Schema + Migration M014 | ~300 | #24, #25 合并后 |
| PR-B | Retrieval Layer (7 channels + RRF) | ~400 | PR-A 合并后 |
| PR-C | External Interface (CLI + MCP + IPC) | ~300 | PR-B 合并后 |

### 第三步：Phase 2/3 设计对齐

与 Varun 直接沟通后，再确定域标签、全局实体解析、shared scope 的实现方案。

## MSLM 发布状态

| 平台 | 包名 | 版本 | 状态 |
|------|------|------|:--:|
| PyPI | `mslm-memory` | 4.1.0 | ✅ 已发布（基于上游 v3.6.16） |
| npm | `mslm-memory` | 4.1.0 | ✅ 已发布（基于上游 v3.6.16） |
| 文档 | `docs/` (中英双语) | — | ✅ 已完成 |
| Hermes Provider | 设计规格 v1 | — | ✅ Spec 已完成，待实现 |

## 时间线

| 阶段 | 动作 | 时间 |
|------|------|------|
| 已完成 | 提交 2 个 bug fix PR | 2026-05-17 |
| 已完成 | MSLM 品牌化 + 双包发布 | 2026-05-30 ~ 06-01 |
| 已完成 | Hermes MemoryProvider spec | 2026-06-02 |
| 当前 | 回复 Varun，确认 PR 拆分方案 | 2026-06-15 |
| 下一步 | #24/#25 合并后，开始 PR-A (Schema) | 等待上游 |
| 后续 | PR-B → PR-C → Phase 2/3 设计对齐 | TBD |
