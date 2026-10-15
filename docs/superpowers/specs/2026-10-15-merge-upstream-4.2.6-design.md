# 上游 4.2.6 合并设计（merge upstream v4.2.6）

- 日期:2026-10-15
- 状态:设计已逐节批准(护栏退役/迁移重编号/版本依赖/存活审计/测试生产门/文档回写);spec/plan 审阅修正已落(2026-10-15 二轮)
- 上游目标:`v4.2.6`(qualixar/superlocalmemory),merge-base 仍为 4.1.17(`5bfa47c9`),新增 **2,036** 提交(4.1.18-4.1.24 + 4.2.0-4.2.6,上游 10/3-10/14 冲刺)
- 需求来源:主人指令"启动 merge plan 流程"(2026-10-15);上游形势摸底结论见本文 §1
- 前置事实:我们的 §19 分数护栏已生产值守三周,判别实验通过(archive 恒 0,active 9,503 / warm 3,259,daemon 全绿,9/26 单轮日志 `2,269 guard-refused`)——护栏使命完成,由上游根因修复接棒

## 1. 背景与撞车点

上游本轮以远超以往的节奏发布(14 个版本/12 天),核心主题:召回性能冲刺(大库亚秒召回、每题答案检查)、记忆健康 WP-19(stub 标记/富化/擦除完整性)、EmbeddingGemma 2 评测、宿主生态爆发(VS Code/Cursor/Claude/Grok/Muse/copilot-cli/openclaw、`slm connect`/`slm tools`、MCP registry)、隐私政策。

与本 fork 的三个硬撞车点:

| 撞车点 | fork 侧 | 上游侧 | 处置(已批准) |
|---|---|---|---|
| §19 同源问题 | 分数护栏(9/25 上线,`be6e30cb`) | `74cb08b8`(10/5,首见于 **4.1.21**):`core/langevin_tier.py` 每轮把半径**重测**为 `1-R(t)`,SDE 步进移除——根因修复 | **采纳上游,退役护栏**(§2) |
| 迁移编号 M052 | `M052_provenance_kind_column`(fork 私有) | `M052_memory_kinds` … 已用至 M063 | **fork 重编号 → M064**(§3) |
| 版本号 4.2.0 | fork 自行 sweep 的 4.2.0 | 真上游 4.2.0-4.2.6 | **对齐 4.2.6**(§4) |

其余 fork 补丁面:hermes daemon-first 路由、temporal 锁容忍、ELC 时钟(store_scaled_strength)、correction 幂等、vec0 加载、per-request profile(#127 已在上游)——上游未碰其中多数文件,存活风险集中在 `maintenance.py` 与 `database.py` 两处文本冲突(§5)。

## 2. 护栏退役(已批准)

采纳上游 `langevin_tier.py` + `_langevin_pass` 全案;fork 侧退役:

- 删除 `_ZONE_COLDNESS`/`_is_colder`/`_guard_zone_updates`/`_current_zone_map` 四个 helper 与三处接线(步骤 1a/1b/Fisher);`_persist_lifecycle` 恢复上游形态(去掉 lifecycle=None 语义——上游方案下无人再传 None)。
- 删除护栏单测(`TestIsColder`/`TestGuardZoneUpdates`/`TestEveryRadiusWriteSiteIsGuarded`/`TestGuardAgainstLiveMaintenance`——前三者是 helper 自证;后者 3 条中 radius-cannot-archive 由下行行为回归网承接,两阶段冷却与 warming-is-free 是护栏机制自证,机制消失后行为由上游 `test_maintenance_never_archives_a_used_memory` 套件覆盖);`counts["langevin_guard_refused"]` 与完成日志字段随接线一起退役。
- **保留行为回归网**:把"饱和 position 不得归档在用记忆"的集成断言移植到上游实现上——注意上游 `_langevin_pass` 只读 access_count/age/importance 重测半径 `1-R(t)`,**不读 `fact_retention.retention_score`**;断言夹具以"在用"(access 多/age 新)表达,不再以 score 为权威——我们 9/25 修的保证由测试继续背书,代码不再背书。
- M051 重跑/生产序列回放测试(`TestM051Rerun`/`TestProductionReplay`)中对护栏的断言改为对上游行为断言;迁移本身(M051/M043)不动。
- 运维笔记 §21 补收尾注记:"护栏由上游 4.1.21 根因方案(langevin_tier)接棒退役;三周判别通过"。

**生产语义等价性依据**:fork 手术终态(9/25)本就是 zone≡分数;上游方案下半径恒等于 1-R(t),zone 由 #136 推导的边界(1-阈值)映射,同样收敛到分数域——两方案在"tier 只跟使用与年龄"上等价,上游是根因(漂移源头消失),fork 是症状拦截。

## 3. 迁移重编号(已批准)

- `M052_provenance_kind_column` → **`M064_provenance_kind_column`**:改模块文件名、`NAME`、目录登记——登记点在 **`_migration_catalogue.py`**(上游已把 `MIGRATIONS`/`DEFERRED_MIGRATIONS` 抽到此新文件,`migration_runner` 仅 import;fork 旧条目随 fork 侧列表消失,须在 catalogue 的 `DEFERRED_MIGRATIONS` 末尾重新登记);依赖保持 `(M046,)`(M046 双方同内容);`_schema_version.SUPPORTED_SCHEMA_VERSION` 52 → **64**(上游 63 + fork 一位)。
- 版本一致性测试不变量(上限=最大序号)自然保持;`test_provenance_kind.py`、`test_backward_compat_405.py` 两处名称引用同步改。
- **生产库安全**:旧 `M052_provenance_kind_column` 记录保留在 migration_log(历史事实);M064 首跑走既有 PRAGMA 列守卫发现列已存在→幂等空跑落账。上游 M052-M063 正常首跑(全部 additive)。

## 4. 版本与依赖(已批准)

- 版本全套 sweep → **4.2.6**(pyproject、CLAUDE.md.fragment、plugin/宿主清单等上次 sweep 同批文件;以 `git show v4.2.6:pyproject.toml` 为基准逐一对齐)。
- 新依赖:`keyring==25.7.0`;钉版更新 `sentence-transformers==5.6.1`/`transformers==5.10.4`/`torch==2.13.0`。上游注释明确 5.17+ 才破坏 nomic 的 `get_extended_attention_mask`,5.10.4 安全;miniconda 测试环境 editable 重装后跑 import 链与 embedder 冒烟。

## 5. fork 补丁存活审计(逐项:解冲突或测试证明)

| fork 补丁 | 上游动作 | 处置 |
|---|---|---|
| 分数护栏(本次退役) | maintenance.py 重写 + langevin_tier.py 新建 | §2 |
| per-request profile(#127 已进上游,4.1.14) | 上游有演化 | 以上游为准;fork 既有 profile 测试群全跑,语义不回退才算过 |
| provenance_kind 全链(models/DB/MCP/scan) | database.py 有冲突面 | 保留 fork;解 `get_all_facts` 文本冲突后跑 provenance 测试群 |
| hermes daemon-first 路由(f4788225) | 未碰 | hermes 测试群证明 |
| temporal 锁容忍(f21790dd) | 未碰 | admission/temporal 测试群证明 |
| ELC 时钟 store_scaled_strength(f21790dd) | **未碰——上游 ELC Step 3 仍是小时钟** | 保留 fork 补丁;上游 PR 机会(P2)存活 |
| correction 幂等(CorrectionPredecessorBusyError) | review-gate 演化(d489f2a4 等 3 条) | 对账合流;correction 测试群全跑 |
| vec0 sqlite_vec 加载(0811cb56) | code_graph/database.py 在冲突清单 | 上游为基+fork 加载补丁移植,vec0 测试证明 |
| WAL/日志策略修复(main 内,f4dd873c 等) | write_coordinator、infra/backup_obligations 等在冲突清单 | 上游为基,`apply_journal_mode` 等 fork 补丁逐点移植,WAL 测试证明 |
| `updated` 死键(unified_daemon/tools_v33) | 可能仍死 | 只登记不修(独立小 PR 候选,上游贡献清单) |

## 6. 测试与生产门(已批准)

- worktree(`/tmp/slm-merge-426`,分支 `merge/upstream-4.2.6`)上一次性 `git merge v4.2.6`,冲突按 §2/§5 策略解。
- 全量 gate:`pytest tests/ -q -n 8`;基线=main 现有 16 红(12 README 契约 + test_is_stale + hermes inventory + enrich×2);**验收标准=不新增红**——上游 README/宿主生态重写预计消掉部分积压红,算白赚。
- **生产部署独立于 merge**:main 合并推送后、daemon 重启前,生产仍在跑现役代码(editable 指向的 main 只有重启才重载)。部署时机=merge 全绿 + 主人确认,按有序重启铁律(gateway 先停→daemon 重启→gateway 后起)。
- 部署后验收:health 全绿;首轮维护日志出现上游格式(无 guard-refused 字段);zone 分布不倒退(archived 不回升);三发黄金探针(米家日常/迷雾小镇/Fuse);迁移日志 M064 幂等空跑 + M052-M063 首跑 complete。

## 7. 文档回写

- 上游贡献清单:P1 → "上游已修(自 4.1.21,74cb08b8 langevin_tier,不同方案;fork 护栏 9/25 先行生产验证,三周判别通过,§21)";P2(ELC 时钟)确认存活;P3(correction 幂等)标注"上游 review-gate 演化,新颖性待查";`updated` 死键登记为新候选。
- 运维笔记 §22:merge 回执(撞车点处置、gate 数字、部署验收、迁移日志)。

## 8. 风险与回滚

- 最大风险:maintenance.py 双改重写(我 +147 行 vs 上游 -146 行同文件)——解冲突时以**上游为基底**,fork 侧只保 §5 表中"未碰"项,避免手搓混合体。
- database.py 召回重构(预算/排队)与 fork provenance 过滤的冲突:语义正交(一个是读路径性能,一个是读路径过滤),文本冲突按"上游结构 + fork 过滤参数"合。
- 回滚:merge 在 worktree 隔离,main 不动;生产回滚点=merge 前备份 + 现役代码。
- 判别实验已历史性通过(§1 前置事实),本 merge 不依赖任何隔夜观察。

## 9. 非目标

- 不趁 merge 顺手修 16 个基线红(上游修好的白赚,fork 侧的另行立项)。
- 不在本 merge 提任何上游 PR(P2 材料随 merge 后重整)。
- 不动生产配置/宿主接入(上游新增的 VS Code/Cursor 等宿主生态,是否启用由主人另行决定)。
