# CJK Answer-Support 校准实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 CJK 库拟合 answer-support 配方（`ANSWER_SUPPORT_CJK_V1`）并以配置项接入生产，使独特词精确命中回归且垃圾查询弃权，验收后移除休止态 drop-in 92。

**Architecture:** 独立 CJK 语料模块（复用 EN 机器、中文人读填充模板、独立 fixture 目录与 digest，EN 模块字节不动）→ eval 加 `--corpus` 选择器 → 手写四份 CJK fixture（锚点/真实/胡说/不可答，镜像 EN 的 22 类语义）→ 跑特征行 → fit 出配方块 → `support_recipe.py` 注册 + `retrieval.answer_support_recipe` 配置选择 → 生产验收硬标准。

**Tech Stack:** Python 3.13、pytest、numpy/scipy（fit 已用）。测试命令：`env -u ALL_PROXY -u all_proxy PYTHONPATH=<repo>/src ~/miniconda3/bin/python -m pytest <paths>`（miniconda，无 .venv）。

**Spec:** `docs/followup-cjk-answer-support-calibration.md`（brief：工具链命令链/套件结构/验收硬标准/上游打包角度）+ 运维笔记 §25（病理与休止态背景）。

## Global Constraints

- **EN 侧字节不动**：`answerability_corpus.py` 的 `corpus()`/`digest()` 输出不得变化（EN 基线与已建缓存 `/tmp/ansb-smoke/cache/d21fd3d53039f6e6/` 依赖它）——CJK 一切放新文件；Task 1 有 digest 回归测试钉死。
- **确定性**：CJK 语料生成只用 `hashlib`（沿用 EN `_Stream` 机制），同代码同库同序。
- **fixture schema 逐字对齐 EN**：anchors `{key, content, kind, project}`；real `{qid, category, question, expected: [keys]}`；nonsense/unanswerable `{qid, category, question, expected: "abstain"}`。
- **类别语义镜像**：CJK 套件覆盖 EN hand 套件全部 22 类（random_letters→随机汉字串 等），`non_english` 类在 CJK 语境镜像为 `non_chinese`（拉丁文不可答）。
- **验收硬标准**（Task 6，全过才算成）：`九间房缺口`/`阿克希利`/`M3d 基线`/`面纱 maid-veil`/`骏骏` 回归真结果；`今天天气怎么样`/`量子计算的最新进展` 弃权；`米家日常`/`迷雾小镇` 黄金正常。
- **生产纪律**：Task 6 前不碰生产 daemon；验收经主人确认后执行；drop-in 92 移除=最后一步。
- 卫生：落盘文件无 `/home/kai-remote` 真实路径。

---

### Task 1: CJK 语料模块 + eval `--corpus` 选择器

**Files:**
- Create: `scripts/quality/answerability_corpus_cjk.py`
- Create: `tests/fixtures/answerability_cjk/`（目录；`anchors.jsonl` 由 Task 2 填充，本任务先放 3 行种子锚点使模块可跑）
- Modify: `scripts/quality/answerability_eval.py`（`--corpus {en,cjk}` 默认 en；`_load_corpus_module`/`build_store`/`_questions`/digest 调用点随选择器）
- Test: `tests/test_quality/test_cjk_corpus.py`（新建）

**Interfaces:**
- Produces（Task 2/3/4 依赖）：
  - `answerability_corpus_cjk.corpus() -> list[Memory]`、`load_questions(name)`（读 `tests/fixtures/answerability_cjk/{name}.jsonl`）、`digest()`、`CORPUS_VERSION = "answerability-corpus-cjk-v1"`
  - eval：`--corpus cjk --suite hand` 读 CJK 模块的 fixture；`--cache` 下 CJK digest 独立缓存目录
- Consumes：EN 模块的 `Memory`、`_Stream`、`_FILLER_COUNTS` 机器与 hex/id 生成器（import 复用，语言中性）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_quality/test_cjk_corpus.py
"""The CJK corpus module must not perturb the EN corpus, and vice versa."""
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, _REPO / "scripts" / "quality" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_en_digest_is_byte_stable():
    en = _load("answerability_corpus")
    assert en.digest() == "d21fd3d53039f6e6"  # the cached store's digest


def test_cjk_digest_differs_from_en():
    en, cjk = _load("answerability_corpus"), _load("answerability_corpus_cjk")
    assert cjk.digest() != en.digest()


def test_cjk_corpus_loads_anchors_and_unique_rows():
    cjk = _load("answerability_corpus_cjk")
    rows = cjk.corpus()
    anchors = [r for r in rows if not r.key.startswith("fill-")]
    assert len(anchors) >= 3
    assert len({r.key for r in rows}) == len(rows)


def test_cjk_filler_carries_chinese_human_templates():
    cjk = _load("answerability_corpus_cjk")
    rows = [r.content for r in cjk.fillers()
            if r.kind in ("meeting", "maintenance")]
    assert rows and all(any("\u4e00" <= ch <= "\u9fff" for ch in t) for t in rows)
```

- [ ] **Step 2: 跑测试确认失败**（`answerability_corpus_cjk` 不存在 → collection error）
- [ ] **Step 3: 实现 CJK 模块**——结构：`from answerability_corpus import Memory, _Stream, interleave, _slice_of, _lifecycle_of, _date`（注意 EN 模块用相对路径加载，需 `sys.path` 注入或 importlib 双载——按 eval 的 `_load_corpus_module` 现有做法：CJK 模块内 `import importlib.util` 加载 EN 模块取机器）；`FIXTURES = Path(__file__).parents[2]/"tests"/"fixtures"/"answerability_cjk"`；`anchors()`/`fillers()`/`corpus()`/`load_questions()`/`digest()` 同构 EN；**人读填充模板全部中文化**（`_meeting`/`_maintenance`/`_session`/`_stub` 等的文本模板写中文，hex/id 部分保留 EN 机器）；种子 `anchors.jsonl` 放 3 行（code-note/decision/name 各一，中文内容）。
- [ ] **Step 4: eval `--corpus` 接线**——`p.add_argument("--corpus", default="en", choices=["en", "cjk"])`；`_load_corpus_module(name)` 按 name 加载对应文件；`build_store`/`_questions`/digest 全走所选模块；缓存目录含 digest 自然分离。EN 默认路径行为字节不变。
- [ ] **Step 5: 跑测试通过 + 全量 fixture schema 抽查** → commit `feat(quality): CJK answerability corpus module + eval --corpus selector (EN digest untouched)`

### Task 2: CJK 锚点语料（~100 行）

**Files:**
- Modify: `tests/fixtures/answerability_cjk/anchors.jsonl`（3 → ~100 行）
- Test: `tests/test_quality/test_cjk_fixtures.py`（新建；schema + 唯一性 + kind 集）

**Interfaces:**
- Produces: ~100 锚点，key 前缀 `cjk-`（如 `cjk-code-daemon-wait`/`cjk-name-骏骏`），kind 集 = EN 锚点 kind 集（code_note/version/decision/event/name/paraphrase/semantic_note + 少量 Latin-identifier 锚点支撑 id 类问题）。
- Consumes: Task 1 的 fixture 目录与 loader。

- [ ] **Step 1: 写 schema 失败测试**（行数 ≥90；key 唯一且 `cjk-` 前缀；kind ∈ 合法集；content 非空且长度 ≥10；中文锚点 content 含汉字；id/code 类锚点保留拉丁标识符）
- [ ] **Step 2: 创作锚点**——**双语质量要求写进提交说明**：内容取材"一年期中文 agent 记忆库"的真实感——米家/迷雾小镇世界观条目、运维决策（systemd/守护/journal 模式）、版本记录（mslm 4.2.6）、人名（骏骏/知惠/主人）、代码注记（含函数名与秒数等可问细节）、**paraphrase 对**（同一事实两种中文说法）——每条都含"问题可指向的具体细节"（数字/专名/独特词），EN 锚点的 `ensure_daemon 12 seconds` 式可问性是标杆。
- [ ] **Step 3: 测试过 → commit** `feat(quality): CJK anchor corpus (~100 rows, EN-anchor questionability bar)`

### Task 3: CJK 问题套件（real ~110 / nonsense ~65 / unanswerable ~33）

**Files:**
- Create: `tests/fixtures/answerability_cjk/real.jsonl`、`nonsense.jsonl`、`unanswerable.jsonl`
- Test: `tests/test_quality/test_cjk_fixtures.py`（扩展）

**Interfaces:**
- Consumes: Task 2 锚点 key 集（`expected` 必须全部命中）。
- Produces: 类别分布镜像 EN hand（22 类；`non_english`→`non_chinese`）。

- [ ] **Step 1: 写失败测试**——real 行数 ≥100 且类别覆盖 EN 全 22 类的 CJK 对应；`expected` 全部存在于 anchors；qid 全局唯一；nonsense/unanswerable 的 expected=="abstain"；nonsense 类别含 `随机汉字`/`中文词胡搭`/`id_like_unstored` 对应物。
- [ ] **Step 2: 创作问题**——每类语义直译到中文语境（如 `shares_one_token`=问题与某锚点共享一个独特词但问的是另一件事；`near_miss`=问库中相邻但不存在的事实）；**验收探针五词必须作为 exact/paraphrase 类问题在 real.jsonl 中有对应**（九间房缺口/阿克希利/M3d 基线/面纱 maid-veil/骏骏 各至少 1 题，锚点即生产语义的合成等价物）。
- [ ] **Step 3: 测试过 → commit** `feat(quality): CJK question suites mirroring all 22 EN categories`

### Task 4: 建库 + 特征行 + 拟合

**Files:**
- 产出（非提交物）：`/tmp/ansb-cjk/cache/`（CJK 合成库）、`/tmp/ansb-cjk-rows.jsonl`、`fit.json`
- Modify: 无代码（若 fit 暴露语料缺陷 → 回 Task 2/3 修，此为预期迭代）

- [ ] **Step 1: 建库跑行**（后台，~20min）：`env -u ALL_PROXY -u all_proxy PYTHONPATH=src ~/miniconda3/bin/python scripts/quality/answerability_eval.py --corpus cjk --work /tmp/ansb-cjk --profiles a --suite hand --rows /tmp/ansb-cjk-rows.jsonl`
- [ ] **Step 2: 拟合**：`... answerability_fit.py --rows /tmp/ansb-cjk-rows.jsonl --out /tmp/ansb-cjk-fit.json`；exit 0（held-out 可分）为过关，exit 1 → 读 trade-off 曲线，回语料补强（最常见的两类：nonsense 太容易/太难得分 → 调胡说类难度；paraphrase 对不足 → 补锚点）
- [ ] **Step 3: 记录配方块与 held-out 表**进本任务的进度笔记（拟合数字是 Task 5 的输入）

### Task 5: 配方注册 + 配置选择

**Files:**
- Modify: `src/superlocalmemory/retrieval/support_recipe.py`（`ANSWER_SUPPORT_CJK_V1` = Task 4 配方块，同数据类字段；`ACTIVE`/选择函数）
- Modify: `src/superlocalmemory/core/config.py`（`RetrievalConfig.answer_support_recipe: str = "auto"`，注释说明 auto/v2/cjk-v1）
- Test: `tests/test_retrieval/test_support_recipe_selection.py`（新建）

**Interfaces:**
- Produces: `select_recipe(config) -> SupportRecipe`（供 support_gate 的 `features`/`assess` 默认参数链使用——**沿 support_stage.py:38 已正确传 config.retrieval 的路径**接，不修 B7 的 engine.py 传错对象路径，B7 留上游）；配置 `answer_support_recipe="cjk-v1"` 时 CJK 配方生效。
- Consumes: Task 4 的拟合数字。

- [ ] **Step 1: 失败测试**——auto(默认)=v2；显式 cjk-v1=CJK 配方（threshold/weights 与注册块逐字段相等）；非法值回落 v2 + warning；env `SLM_RECALL_NO_SUPPORT_GATE=1` 时选择函数仍可被调用（纯函数）。
- [ ] **Step 2: 实现**——配方块按 support_recipe.py 现有文档惯例（fitted_on 注明 corpus digest 与日期）；`switched_off` 不动。
- [ ] **Step 3: 回归**：`tests/test_retrieval/` 全组 + hermes provider 组 → commit `feat(retrieval): ANSWER_SUPPORT_CJK_V1 + answer_support_recipe selector`

### Task 6: 生产验收（🔒 主人逐步确认）

- [ ] **Step 1: 🔒 配置**：`retrieval.answer_support_recipe="cjk-v1"` 写入 `~/.superlocalmemory/config.json`；**保留 drop-in 92 先行灰度**（env 关门仍生效，配方先在 shadow 验证）——用 Task 4 的 CJK 库引擎（非生产）以配方开+门开跑 hand_cjk 全套：真实类命中、胡说类弃权 ≥ EN v2 基线的比例。
- [ ] **Step 2: 🔒 生产切流**：删 drop-in 92 → `systemctl --user restart superlocalmemory` → env 验证（无 SLM_RECALL_NO_SUPPORT_GATE）→ **九发硬标准探针**（五独特词+两垃圾+两黄金，全部达标本步才过）。
- [ ] **Step 3: 🔒 首夜守望**：02:00 窗后知惠/主人复核（垃圾查询不因配方切换重新上菜）。
- [ ] **Step 4: 文档回写**——运维笔记 §27（配方数字/验收记录/drop-in 移除）；brief 标记完成；贡献清单：CJK 配方+语料套件上游 PR 材料就绪（挂 B6）。
- 回滚：恢复 drop-in 92 或配置回 `auto`，重启即回休止态/EN 配方。

---

## Self-Review

- **Spec 覆盖**：brief 五步（语料创作/eval+fit/配方注册+配置选择/验收硬标准/上游化）→ Task 2-3/4/5/6/6-Step4 一一对应；"地板阈值顺带复测"（brief 提及）纳入 Task 4 迭代回路（若 fit 可分则地板不动，骏骏类由配方端解决；不可分再议 `min_semantic_evidence`）。
- **占位符扫描**：Task 2/3 是创作型任务，内容规格以"类别语义+质量标杆+验收探针硬约束"具体化，非代码模板可逐字给出——已给 schema 测试代码与每类语义定义；无 TBD。
- **类型一致性**：`corpus()/load_questions()/digest()` 签名与 EN 模块一致（eval 泛调用依赖）；`select_recipe(config)` 与 support_stage 现有传参约定一致；配方数据类 SupportRecipe 复用。
