# Langevin 链生命周期护栏 + 对齐修复规格设计

- 日期:2026-09-24
- 状态:设计已批准(三节逐节确认),待 spec 审阅
- 需求来源:运维笔记 §19(知惠诊断)+ #136 上游生命周期修复漏网(本 fork 的两次"手术-翻案"循环实证)
- 上游叙事:P1(见 `docs/上游贡献清单.md`)——"lifecycle guard: don't let Langevin positions supersede the store-scaled retention score"
- 前置:两次判别实验已证"只改分数、不改 position"的修复当晚即被翻案;本设计先修写者,再修数据

## 1. 背景与目标

#136(4.1.15/16)修了时钟:`batch_compute_retention` 走 `store_scaled_strength`,批量评分不再误归档;**但维护层 Langevin 链(step 1b)的 `radius→zone` 换算绕过了 retention_score 的权威**——`maintenance.py:468-469` 与 `:552` 的写入口直接用 `compute_lifecycle_weight(position) → get_lifecycle_state(weight) → zone`,position 饱和(≥0.99)后 zone 被 radius 独断,分数信号永远进不来。实测:饱和占比 100%(500 条样本全部半径≥0.99),20 条采样 20/20 半径与分数偏差>0.76。

**目标**:`zone` 列回到单一权威——EbbinghausCurve/batch 的 retention_score 意图;Langevin 半径可影响检索权重(`compute_lifecycle_weight`),但不能推翻分数的 zone。M051 重跑(清 position)+ M043 恢复(分数信号)+ 补种防线(不覆盖既有 position)按序补完闭环。

**非目标**:不改 Langevin step 的数学步(`step` 是纯动力学,不改时钟语义);不改 langevin.py 边界定义(`_RADIUS_COLD = 1-archive_threshold` 已是 #136 的连通);不废 langevin_position(纯数学层与检索权重均依赖,M051 的"重算而非重采样"依赖 position)。

## 2. 已批准的关键决策

| 决策点 | 结论 |
|---|---|
| 对齐方式 | **护栏**(写入口拦截,不动数学步;最小 surgical) |
| 上游叙事 | **对齐 #136 意图**(PR 挂 #136;"one authority 被两处代码遵守") |
| M051 扫空 | **M051 重跑**(apply 可重入;配合补种防线清种顺序) |
| 崩溃方案(废 position) | **否决**:M051 依赖 position 做重算,4.1.17 实测 9,882 条迁移依赖它 |

## 3. 生命周期权威模型

```
权威域           行为                                      判据/工具
─────────────  ─────────────────────────────────────  ─────────────────
1. 分数域       batch_compute_retention               #136 修复(正确时钟)
   (写者一)    → zone                                  EbbinghausCurve.lifecycle_zone

2. 半径域       step 1a: 播种(position 种子)           _retention_radius(1-R(t))
   (写者二)    → zone                                  _seed_langevin_position
                step 1b: step(只推 position)          get_lifecycle_state
                → zone(半径)                           半径→zone 换算

3. 检索域       compute_lifecycle_weight(position)    compute_lifecycle_weight
   (消费)      → 权重                                   消费 langevin_position

4. 文档域       zone 定义常量                          langevin.py:59-77
   (参考)                                             _RADIUS_COLD = 1-archive_threshold
```

**修复点(蓝色)**:step 1b 的 `radius→zone` 写回处加**分数护栏**——写 zone 时,若新 zone 比 `retention_score` 意图的 zone 更冷(更远离 active),则拒写(或提回新 zone)。半径仍参与权重(消费面),但永远不能推翻分数的 zone。

**护栏的保守设计**:半径与分数的换算口径(`_RADIUS_COLD = 1-archive_threshold`)是 #136 自己定的,护栏不改这个换算,只加"不推翻分数"的不等式——修复量最小,上游 review 语境完全在 #136 内。

## 4. 修复点与顺序

四个修复,顺序不能反:

```
① 护栏(代码,先修) ─→ ② M051 重跑(清 position) ─→ ③ M043 恢复(分数信号) ─→ ④ 补种防线
```

**① 护栏**(`core/maintenance.py:468-469` 与 `:552`):

```python
weight = ld.compute_lifecycle_weight(position)
proposed = ld.get_lifecycle_state(weight).value
current = fact_retention_or_atomic_lifecycle_row
if _is_colder(proposed, current):
    lifecycle = current      # 半径想翻案到更冷 zone,但 retention_score 没发话——拒写
else:
    lifecycle = proposed
```

`_is_colder` 按 zone 序(active < warm < cold < archive < forgotten)判断:允许一切升温,拒绝任何无分数依据的降温。两处写入口共用同一护栏。

**② M051 重跑**:`M051.apply()` 已可重入(清 position,IS NOT NULL 才 UPDATE);连跑两次第二次零影响。清掉饱和的 position(3,373 条),让生命周期回到纯分数域。**注意**:重跑不区分"饱和"与"健康"position,全部清——这是 #136 的设计意图("重算"意味着先扫再种,不是只清),补种立即重建。

**③ M043 恢复**:2,724 条 score≥0.8 归档行提回(M043 的恢复 predicate 现成);在护栏就位(写者不再翻案)后执行。必须在 ② 之后(M043 依赖 M051 清出的 position 空位,否则 position 残留又会被 step 推出饱和)。

**④ 补种防线**:`_seed_langevin_position` 加"已存在 position 不覆盖"守卫——补种不推翻既有 position;清扫-播种顺序由代码保证,33 分钟差不复发。

**每步的生产含义**:① 纯代码(TDD);②③④ 同轮数据操作(daemon 有序停止,②③ 数据,④ 代码部署)。

## 5. 测试与验收

**单元测试**(`tests/test_core/test_lifecycle_guard.py`,新建):

1. 护栏方向正确:半径提议"更冷"→ 拒写;"更暖"→ 放行;同 zone → 放行
2. 无分数依据的降温被拒:`retention_score=0.9, zone=active` 的行,半径提议 archive → 拒写,zone 保持 active
3. 分数依据充分的降温放行:`retention_score=0.1` 的行,半径提议 archive → 放行
4. step 1a 与 step 1b 一致:两个写入口都走同一护栏
5. langevin_weight 不受影响:权重计算照旧

**集成测试**:

6. 完整链路:`set_fact_lifecycle_zone(batch zone=warm)` → 维护 step 提议 archive → 护栏拒写;重算分数到 archive → 护栏放行
7. M051 重跑可重入:连跑两次第二次零影响;position 清零且不被后续补种覆盖(④ 防线)
8. 端到端生产仿真(仿 provenance e2e):3,373 条饱和 position 清出 → M043 恢复 → 召回可用面从 26.8% 恢复(米家 KNN top-50 不再有"半径提议 archive 被护栏拦下")

**生产验证**(daemon 有序停止下):
- ② 后:position 全部 NULL,`fact_retention.last_computed_at` 前进
- ③ 后:归档从 66% → 目标 <5%(M043 提回 score≥0.8;M051 清 position 后 step 不翻案)
- ④ 后:夜间 02:00 维护窗再扫一次——**判别实验**(与 §13/§17 一致):维护窗过后 zone 不批量回升

## 6. 上游 PR 打包(P1)

- **PR 标题**:"lifecycle guard: don't let Langevin positions supersede the store-scaled retention score"
- **叙事**:"#136 修复了时钟,但只修了两处:batch 的 retention 与 ELC 的 retention。维护层 step 1b 的 radius→zone 换算绕过了 retention_score 的权威——position 饱和后,zone 被 radius 独断,分数信号进不来。这个 PR 给写入口加了分数护栏:半径仍可影响检索权重,但不能推翻分数的 zone"
- **证据链**:#136 的边界定义(langevin.py:59-77 的"两套坐标不漂移"注释)、生产实证(夜间 02:00 单 tick 3,281 条饱和翻案)、§13/§17 判别实验(手术无护栏当晚即重演 vs 有护栏站得住)
- **关联 PR**:P2(锁容忍)独立不冲突,可分别提;P1 是更高质量(挂 #136)

## 7. 后续(不在本 spec 范围)

- M043 恢复后的 BM25 覆盖验证(archive 归零后覆盖率是否随新写入回升)
- embedder flap 观察项(14:47 后持续零复发,维持守望)
- 上游 PR 的实际提交(等用户拍板;材料已齐)
