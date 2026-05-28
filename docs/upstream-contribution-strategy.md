# 上游贡献与公布策略

> 2026-05-17 — 分析当前 fork 状态，识别上游原生 bug，制定公布策略

## 当前状态

- **上游**: `qualixar/superlocalmemory`，持续活跃开发
- **Fork**: `kenyonxu/superlocalmemory`
- **分歧点**: `19f051c` (v3.4.45, 2026-05-13) — 已 rebase 至上游最新
- **Fork 新增**: ~89 commits，含 multi-scope memory (Phase 1+2+3+2B) + scope-e2e + 多项修复
- **上游 Issue 响应**: 几乎不回复 Issue（最早 2 月份至今无响应），但会选择性合并 PR
- **我们的 RFC Issue**: [#20](https://github.com/qualixar/superlocalmemory/issues/20)，12 天零评论
- **第一步 PR 已提交**: [#24](https://github.com/qualixar/superlocalmemory/pull/24) WAL busy_timeout, [#25](https://github.com/qualixar/superlocalmemory/pull/25) logger.exception

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

## 公布策略：三步走

### 第一步：Bug 修复 PR ✅ 已完成（2026-05-17）

已将 2 个上游原生 bug 提交为独立小 PR：

- **[PR #24](https://github.com/qualixar/superlocalmemory/pull/24)** — fix: set busy_timeout before journal_mode=WAL in _enable_wal
- **[PR #25](https://github.com/qualixar/superlocalmemory/pull/25)** — fix: use logger.exception for engine init failure to capture traceback

每个 PR 只改核心代码 1-2 行 + 对应测试，review 成本极低。

**等待上游审阅中。**

### 第二步：Multi-Scope 功能 PR（试探）

将 Phase 1 作为独立功能 PR 提交，观察反应：

- 合并 → 继续提交 Phase 2，共建一套发行
- 关闭/沉默 → 上游不感兴趣，启动独立发行

### 第三步：根据反馈决定最终路径

| 上游反应 | 策略 |
|----------|------|
| 全部合并 | 作为核心贡献者，共用 PyPI 包 |
| Bug 修复合并，大功能被拒 | 长期维护 fork，定期 rebase，新包名独立发布 |
| 连 Bug 修复都不理 | 硬分叉，完全独立 |

## 独立发布前置准备

如需独立发布：

- **包命名**: 不能与上游 PyPI/npm 的 `superlocalmemory` 冲突
- **版本线**: 独立版本号
- **README/文档**: 标注 fork 来源、差异点、迁移路径
- **LICENSE**: AGPL-3.0-or-later，保留原始版权声明
- **Changelog**: 完整变更记录，区分 bug fix 和 feature

## 时间线

| 阶段 | 动作 | 时间 |
|------|------|------|
| 本周 | 提交 2 个 bug fix PR | 立即 |
| 1-2 周 | 观察反应，准备 multi-scope PR | 等待 |
| 2-4 周 | 最终决策并执行 | 决策点 |
