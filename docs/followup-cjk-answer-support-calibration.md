# Follow-up: CJK answer-support 校准（本地拟合）

> 建立:2026-10-19 · 来源:运维笔记 §25(休止态 drop-in 92 期间) · 状态:**待排期执行**
> 前置事实已全部踩平,本文件是下一会话的执行 brief

## 目标

为 CJK 库拟合 answer-support 配方(替换 EN 合成语料拟合的 v2),使**独特词精确命中回归**且**垃圾查询弃权**;验收后删 `~/.config/systemd/user/superlocalmemory.service.d/92-support-gate-off.conf` 并重启 daemon。

## 已踩平的地形(2026-10-19 会话)

- 工具链:`scripts/quality/answerability_eval.py`(建合成库+跑特征行,floor off)→ `scripts/quality/answerability_fit.py`(拟合+阈值选择+输出可贴配方块)
- 命令链:
  - `python scripts/quality/answerability_eval.py --work <scratch> --profiles a --suite hand --rows rows.jsonl`(**建库是慢步 ~15min,完成后按 corpus digest 缓存**;`--cache` 可指定缓存位置)
  - `python scripts/quality/answerability_fit.py --rows rows.jsonl [--profile a] [--out fit.json]`
- **合成库缓存已建**:`/tmp/ansb-smoke/cache/d21fd3d53039f6e6/`(2026-10-19 后台跑完;若 /tmp 被清,重建即可)。注意 eval "refusing to build or measure inside own repo; pass a scratch --work folder"
- hand 套件结构:205 题/22 类( nonsense 类:random_letters/in_vocabulary_nonsense/id_like_unstored/shares_one_token;精确类:exact_id/name/version/acronym/paraphrase_pair;**已有 non_english 类 ×7**;audit 类 ×60;near_miss ×6)。语料模块:`answerability_corpus.py`;问题:`answerability_questions.py` 的 SUITES
- 配方结构:`retrieval/support_recipe.py`(ACTIVE=v2,threshold=0.3003;按 `_model_key(model)` 选配方——**按 embedder 模型选,不按语言**;CJK 配方对本库(nomic+CJK 为主)全局适用是可接受的)
- v2 为什么误杀 CJK:`cross_encoder` 特征 Latin-only(中文→`ce_missing=-9.05` 惩罚≈-1.2 logits)+ 拟合语料的 nonsense_similarity/rare_miss 分布与 CJK 分词不匹配;实测独特词 p=0.0149 vs 0.3003
- 地板是另一个独立标定面:`min_semantic_evidence=0.60`(EN 库测得;「骏骏」类人名查询被地板误杀——休止态下仍 0)。校准时用 eval 的 floor-off 特征行顺带复测 CJK 余弦分布,决定是否调 `retrieval.min_semantic_evidence`

## 执行方案(SDD 一会话量级)

1. **CJK 语料+套件创作**(主体工作):在 `answerability_corpus.py`/`answerability_questions.py` 增加中文变体(语料 ~2000 条中文记忆,问题镜像 22 类结构;可参照现有 non_english 类的写法)。命名建议 suite=`hand_cjk`/corpus digest 单独缓存
2. 跑 eval(缓存新建,慢步)+ fit → 得 CJK 配方块
3. `support_recipe.py` 增配方(如 `ANSWER_SUPPORT_CJK_V1`)+ 选择逻辑:按 embedder 模型 + 库语言探测(或简单起见:配置项 `retrieval.answer_support_recipe=cjk-v1` 指定,避开自动语言探测的复杂性)
4. **验收(硬标准)**:删 drop-in 92 → daemon 重启 → 九间房缺口/阿克希利/M3d 基线/面纱 maid-veil/骏骏 全回归;今天天气怎么样/量子计算 弃权;米家/迷雾黄金正常
5. 上游化:配方+CJK 语料套件作为 PR 素材(与 B6 bug 报告联动)

## 关联

- 休止态与差分实验记录:运维笔记 §25
- 上游候选:B6(v2 配方 CJK 误杀)、B7(switched_off 传错对象)、B8(ops resolve 不认 M018 failed 行)
- 附带发现:生产库里有 hermes cron 系统提示词被存成记忆(2 条活 fact:1e346d0a/2939be47,11 个已清声明的 op 同源)——知惠策展决定是否删
