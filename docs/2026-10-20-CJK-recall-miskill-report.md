# CJK 召回误杀专项 — 诊断与校准计划报告

**日期**: 2026-10-20 · **状态**: 根因两层已钉死、休止态稳定、代码链已合并 main、真库校准待启动
**关联文档**: [运维笔记 §25/§26/§27/§28](运维笔记-2026-09-22-桥侧与维护层挂账.md) · [校准 follow-up brief](docs/followup-cjk-answer-support-calibration.md) · [上游贡献清单](docs/上游贡献清单.md) (B6/B7/B8/B9)

---

## 1. 问题与症状

**报告者**: 知惠（hermes 桥侧复核）· **时间**: 2026-10-19

SLM daemon 健康（4.2.6、自愈正常），但召回被"置信过滤"成批误杀：

| 查询类型 | 表现 |
|---|---|
| 独特词（只存在于一条记忆） | `九间房缺口`/`阿克希利`/`M3d 基线`/`面纱 maid-veil` → 80/66/150/118 候选 → **0 结果** |
| 弱相关日常查询 | `骏骏` → 51 候选 → 0 结果（reranker=no_candidates） |
| 有真答案的强匹配 | `米家日常`/`迷雾小镇` → 正常 |
| 域外查询 | `今天天气怎么样`/`量子计算` → 弃权（**正确行为**） |

`show_weak=true` 能掏出那条精确命中的记忆（relevance 0.655），证明**候选在场、被过滤误杀**——不是弃权，是误杀。

## 2. 根因（两层叠加）

### 2.1 主凶：answer-support 门的 v2 配方 CJK 量纲错配

`retrieval/support_gate.py` 的答案支持判定是个 logistic 模型（`support_recipe.py` 的 `ANSWER_SUPPORT_V2`），特征与阈值在**合成英文语料**上拟合：

```
p = 1/(1+exp(-(intercept + Σ weight[f]·feature[f])))  vs  threshold = 0.3003
```

关键错配点（三条合力）：

1. **`cross_encoder` 特征 Latin-only**：`ce_raw` 对中文查询恒走 `ce_missing=-9.05` 缺位惩罚（≈ −1.2 logits）。英文查询真实命中时 CE 给正分，中文精确命中反而被负向钉死。
2. **`nonsense_similarity` 分布漂移**：权重 −16.61 是全表最大负权重。拟合语料的英文胡说题该值低（0.30-0.44），中文库里**真问题**也整体偏高（`骏骏` 0.36+）——一乘就是 −0.5 logits 起步。
3. **拟合库形态差异**：上游拟合在 2k 合成库（Id 密集、hub 结构假造），真库 13k 中文记忆的特征分布整体偏移。

**信封实锤**（`九间房缺口` 被杀时的 `support` 字段）：`best=0.0149, threshold=0.3003` —— 精确命中的 p 值只有阈值的 1/20。

上游自己的 EG2 卡片承认"配方跨模型不可迁移"（v2 的 nonsense 项让 Gemma 上每题损失 ~10 logits）；**跨语种/跨语料同理成立，但上游没给 CJK 库任何告警或替代**——这是我们本次要报上游的 B6。

### 2.2 次凶：地板的 hub 参考系在冷启动时缺失

`retrieval/time_window.py::has_primary_evidence` 的语义通道门槛：
- 有预热 hub 基线 → `hub_corrected ≥ 0.10` 宽松门（daemon 日常态）
- 无基线 → 原始余弦 `semantic ≥ 0.60`（英文库拟合值；中文库真答案聚在 0.60-0.63 一线）

冷启动/快照引擎（如校准采集进程）撞第二层 → 中文库真答案全灭。这就是 §28 的"候选=3 vs daemon 的 107"现场——**B9 候选**（上游）：冷启动时的地板无参考系。

### 2.3 排除项（都已查证非真因）

- embedder worker 单例（pid 文件按数据目录隔离，两命名空间互不挡）
- 巨型 memory 去重（fusion/rerank_pool 无 per-memory 去重；45 个巨型 memory 只是 fact 多）
- reranker 温度（10/16 起即热且持续热）
- 校准状态 `uncalibrated`（出厂默认阈值无本库拟合——这是病根，不是另一层故障）

## 3. 处置与现状

### 休止态（当前生产）

`SLM_RECALL_NO_SUPPORT_GATE=1`（drop-in 92），**门暂时关闭，地板保持开**。效果：

- 五个独特词全回归（0.63-0.66 精确命中可见）
- 代价：垃圾查询漏 1 条 ~0.54 弱结果（可见为弱）——校准前的已知可接受代价
- 「骏骏」类人名短查询仍被地板层误杀（独立于门，随校准一并处理）

### 已落地代码（main）

| 提交 | 内容 |
|---|---|
| `6e792014` | merge: CJK 语料+套件（PR1 材料）+ `ANSWER_SUPPORT_CJK_V1` 配方 + `answer_support_recipe` 选择器（PR2 材料） |
| 之前 | `b0307571` hermes 免包修复 / `ec6218aa` cron 提示词守卫 / `25f98f71` §25 处置 / `c6eb8753`/`e28d418a` §28 修正与真因 |

**注意**：`ANSWER_SUPPORT_CJK_V1`（合成库拟合，threshold 0.2760）在代码里但**生产没有启用**——合成库拟合 ≠ 真库迁移（§27 切流第一探即败的证据）。

### 关键方法论修正（运维笔记 §28 附）

首次切流曾出现**守护进程混淆**：知惠的 04:30 守护用 CLI 直拉 daemon（零 drop-in env）占住 8765，systemd 重启被"端口已占"礼貌挡回，导致三轮开关测试全部打在错进程上。教训：**`/proc/<pid>/environ` 直验进程身份先行于一切差分实验**。守护脚本已改走 systemctl。

## 4. 校准方案（真库本地拟合）

### 物料（全部就绪）

- **题集** `/tmp/slmcjk/real_questions.jsonl`：57 真 / 28 胡说 / 17 不可答；每条 real 的 expected memory_id 经 SQL 验证；五硬探针（九间房缺口/阿克希利/M3d 基线/面纱 maid-veil/骏骏）各含 ≥1 题。
- **快照** `/tmp/slmcjk/realstore/data/*.db`（六库 .backup API 一致性拷贝）+ fact 级 keymap。
- **采集驱动** `/tmp/slmcjk/real_fit.py`（复用 eval 的 `measure_profile` 特征行管线）。
- **配方注册与选择器**（`ANSWER_SUPPORT_CJK_V1` 槽位 + `retrieval.answer_support_recipe` 配置）已合并 main。

### 缺的一环：采集面=生产面

§28 铁证：进程内引擎与 daemon 同查询特征分布不一致（候选 3 vs 107、cosine 0.37 vs 0.6）——**在哪个引擎采的特征，配方只对哪个引擎有效**。采集面必须与生产面同一。

**三方案待拍板**：

| 方案 | 做法 | 成本 | 保真度 |
|---|---|---|---|
| **① daemon HTTP 采集（推荐）** | 采集进程打 daemon `/recall`，对响应做 evidence-attach 后处理成行 | 要加个小端点/后处理 | **最高**（特征定义与生产零差） |
| ② 维护窗采集 | 停 daemon → 快照引擎独占（等 hub 基线回填完成再采）→ 采完起 daemon | 桥侧窗口期断服 | 中（引擎冷启参考系与日常态有差） |
| ③ 代理挂载 | 采集引擎 embedder 走 MCP proxy 挂 daemon worker | 要给 eval 引擎注入 proxy | 中 |

### 执行序列（方案定后）

1. 采集特征行（gate/floor off）→ `answerability_fit.py --rows real-rows.jsonl` 直接吃
2. 注册真库配方（沿用 CJK_V1 的注册点与选择器，新版本号如 `answer-support-cjk-v2-real`）
3. **灰度**（快照库 live 门开）→ **切流**（config pin → 删 drop-in 92 → 重启 → env/版本验证 → 九发硬标准）
4. 首夜守望 + §29 回执 + 上游 PR 材料（B6 病历 + CJK 配方 + 采集管线）

## 5. 上游贡献线索（登记在册）

| 编号 | 内容 | 状态 |
|---|---|---|
| B6 | v2 配方 CJK 误杀（p=0.0149 vs 0.3003 + 五独特词对照数据齐） | 本次报告的主证据链 |
| B7 | `engine.py:896/1842` 把整个 SLMConfig 传给只读 `retrieval.answer_support_gate` 的 `switched_off()`——config 开关在那条路径恒失效，仅 env 兜底 | 已实测（config=false 无效、env 立竿见影） |
| B8 | `ops resolve` 对 M018 failed 行报 "no canonical record"——物化器搁浅操作无管理面收 | 已登记 |
| B9 | 冷启动/快照引擎 hub 探针不可用时落原始地板——检索真空是参考系错配不是没证据 | 本报告 §2.2 |
| PR #266 | ELC 时钟 + `updated` 死键（4.2.7 树上已提） | 等上游响应 |

## 6. 验收硬标准（切流绑定线）

九发探针全过才算校准完成：
- **回归**：九间房缺口/阿克希利/M3d 基线/面纱 maid-veil/骏骏 → 有真结果
- **弃权**：今天天气怎么样/量子计算的最新进展 → `no_confident_match=True`
- **黄金**：米家日常（a4267ec0/00f7db20 至少一名在 top-5）/迷雾小镇 → 正常
- 失败即回滚（config 回 `auto` 或恢复 drop-in 92 + 重启，2 分钟，零数据风险）

---

*ZCode 撰写 · 2026-10-20 · 状态：休止态稳定，等主人拍板采集面方案*
