# Merge upstream 4.0.2–4.0.9 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 upstream(qualixar/superlocalmemory)4.0.2–4.0.9 共 65 个提交安全合入 mslm fork,不丢失任何 fork 独有补丁,不破坏生产 daemon 拓扑。

**Architecture:** 单次 merge `upstream/main`(merge-base 为 `186b93b3`,v4.0.1+3)。冲突分三类处置:品牌层一律 fork 获胜、PR #118 相关一律上游获胜(上游版是我方修复的演进超集)、lock 文件重新生成。文本合并完成后按"fork 补丁存活清单"逐条语义核查,再进测试门与冒烟门。

**Tech Stack:** git(三方合并)、uv(依赖/lock)、pytest(测试门)、gitnexus(变更范围核查,AGENTS.md 强制)。

**Spec:** `docs/upgrade-assessment-2026-08-13-v4-merge-hermes.md`(上一次 v4 merge 的评估与核查清单,§6/§7 模式复用)+ `docs/research-2026-08-21-embeddingservice-daemon-routing.md` §7.1(上游新增 `_warm_guard_embed` 耦合点)。

## Global Constraints

- fork 品牌字段不可被上游覆盖:`pyproject.toml` 的 `name = "mslm-memory"`、`version = "4.2.0"`、`description`、`full` extra 引用名;`src/superlocalmemory/__init__.py` 的版本字符串。
- fork 依赖宽松化不可回退:`torch>=2.11.0` 出现两处(主 dependencies + `search` extra),上游硬 pin `torch==2.11.0` 不得合入。上游本区间依赖零变化(仅 version 字段 4.0.1→4.0.9),因此 pyproject 不需要从上游取任何内容。
- `src/superlocalmemory/integrations/hermes/`(含 tests)为 fork 独有,merge 不应触碰;若 git 报告该目录冲突,立即停止并复查 merge 方向。
- 本仓库 AGENTS.md 强制:提交前运行 `gitnexus_detect_changes()` 核查变更范围;索引过期先 `npx gitnexus analyze`。
- 生产部署(停 daemon、真实数据目录升级)只在全部测试与冒烟通过后进行,且需用户在场确认。
- 全程在独立 worktree 操作,`main` 直到 Task 7 才快进。

---

### Task 1: 隔离工作区与基线确认

**Files:**
- 无文件改动;产出:合并用 worktree + 基线测试记录

**Interfaces:**
- Consumes: 无
- Produces: worktree 路径 `/tmp/slm-merge-409`(后续所有任务在此操作);基线测试结果文件 `/tmp/slm-merge-409-baseline.txt`

- [ ] **Step 1: 确认工作区干净并同步上游引用**

```bash
cd $HOME/github/superlocalmemory
git status --porcelain   # 必须为空;若有未提交内容先处理
git fetch upstream --tags
git rev-parse upstream/main   # 记录,后续 merge 用它
```

- [ ] **Step 2: 创建合并 worktree(隔离 main)**

```bash
git worktree add /tmp/slm-merge-409 main
cd /tmp/slm-merge-409
git checkout -b merge/upstream-4.0.9
```

- [ ] **Step 3: 记录基线测试状态(合并前必须全绿,否则先修再合)**

```bash
cd /tmp/slm-merge-409
python -m pytest tests/ -q 2>&1 | tail -5 | tee /tmp/slm-merge-409-baseline.txt
python -m pytest src/superlocalmemory/integrations/hermes/tests -q 2>&1 | tail -3 | tee -a /tmp/slm-merge-409-baseline.txt
```

Expected: 全量套件与 hermes 92 项全绿(与 main 当前状态一致)。若基线已红,停止 merge,先回报。

- [ ] **Step 4: 记录双方改动指纹(供 Task 3 冲突解决时对照)**

```bash
cd /tmp/slm-merge-409
MB=$(git merge-base HEAD upstream/main)
echo "merge-base: $MB"  # 期望 186b93b3...(v4.0.1-3)
git diff --name-only $MB..HEAD | sort > /tmp/merge-fork-files.txt
git diff --name-only $MB..upstream/main | sort > /tmp/merge-upstream-files.txt
comm -12 /tmp/merge-fork-files.txt /tmp/merge-upstream-files.txt | tee /tmp/merge-overlap.txt
```

Expected: `/tmp/merge-overlap.txt` 含 62 个文件(品牌/plugin  churn 为主 + 7 个代码相关:`pyproject.toml`、`src/superlocalmemory/__init__.py`、`src/superlocalmemory/core/config.py`、`src/superlocalmemory/core/engine_wiring.py`、`src/superlocalmemory/storage/database.py`、`tests/test_ci_guards/test_release_package_surface.py`、`uv.lock`)。`src/superlocalmemory/core/embeddings.py` **不得**出现在 overlap 中(上游未触碰,我方 proxy 补丁文本安全)。

---

### Task 2: 执行 merge 与文本冲突解决

**Files:**
- Modify(冲突解决):`src/superlocalmemory/storage/database.py`、`pyproject.toml`、`src/superlocalmemory/__init__.py`、`CHANGELOG.md`、`tests/test_ci_guards/test_release_package_surface.py`、`uv.lock`、可能的品牌层文件(`plugin/`、`plugin-src/`、`copilot-plugin/`、`codex-plugin/`、`package.json`、`package-lock.json`、`.gitignore`、`README.md`)

**Interfaces:**
- Consumes: Task 1 的 worktree 与 `/tmp/merge-overlap.txt`
- Produces: 一个冲突已解决、未提交的 merge 状态(Task 3 语义核查通过后才 commit)

**冲突解决策略表(严格执行):**

| 文件 | 策略 | 理由 |
|---|---|---|
| `src/superlocalmemory/storage/database.py` | **全取上游** | 上游版是 PR #118 的演进超集:`wal_autocheckpoint=400` 移入 `_connect()` + NO_CKPT warn-once 兜底。我方版本已被上游取代 |
| `pyproject.toml` | **全取 fork** | 上游仅 version 字段变化,依赖零变化;fork 侧含品牌 + `torch>=2.11.0` 宽松化 |
| `src/superlocalmemory/__init__.py` | **全取 fork** | mslm 4.2.0 版本字符串 |
| `CHANGELOG.md` | **fork 为主,顶部追加一行合并说明** | 品牌层 |
| `tests/test_ci_guards/test_release_package_surface.py` | **取上游**,然后跑测试,fork 适配若仍需要再补 | 双方修同一潜伏 bug(见 5ecfd733 与上游 156f3960) |
| `uv.lock` | 删除后 `uv lock` 重新生成 | 机械产物 |
| 品牌层(`plugin/`、`plugin-src/`、`copilot-plugin/`、`codex-plugin/`、`package*.json`) | **逐文件 diff 判断**:上游侧仅为 version 字符串 churn → 取 fork;含实质内容变化 → fork 品牌字段 + 上游内容手工合并 | fork 做过 version sweep(5ecfd733/8c5cfb64),但需防上游改了 SKILL 指令内容 |
| `README.md`、`.gitignore` | README 取 fork(mslm 品牌);`.gitignore` 取并集 | — |

- [ ] **Step 1: 发起 merge(不自动提交)**

```bash
cd /tmp/slm-merge-409
git merge --no-commit --no-ff upstream/main
```

Expected: git 报告若干冲突。若冲突文件超出策略表范围(尤其是 `src/superlocalmemory/integrations/hermes/` 或 `src/superlocalmemory/core/embeddings.py`),`git merge --abort` 并停止上报。

- [ ] **Step 2: 批量解决"全取一方"的冲突**

```bash
cd /tmp/slm-merge-409
# 上游获胜:PR #118 演进版
git checkout --theirs src/superlocalmemory/storage/database.py
git add src/superlocalmemory/storage/database.py
# fork 获胜:品牌与版本
git checkout --ours pyproject.toml src/superlocalmemory/__init__.py README.md
git add pyproject.toml src/superlocalmemory/__init__.py README.md
```

- [ ] **Step 3: 解决 release_package_surface 测试(取上游后验证)**

```bash
cd /tmp/slm-merge-409
git checkout --theirs tests/test_ci_guards/test_release_package_surface.py
git add tests/test_ci_guards/test_release_package_surface.py
python -m pytest tests/test_ci_guards/test_release_package_surface.py -q
```

Expected: PASS。若 FAIL 且失败原因是 fork 的 CI 工作流差异(mslm 的 pypi-publish 形态),对照 `git show 5ecfd733 -- tests/test_ci_guards/test_release_package_surface.py` 把 fork 适配重新打上,再跑至 PASS。

- [ ] **Step 4: 解决品牌层 plugin 冲突(逐文件判断)**

```bash
cd /tmp/slm-merge-409
git diff --name-only --diff-filter=U   # 列出剩余冲突
# 对每个剩余文件:
#   git diff --ours -- <file>   看 fork 侧改了什么
#   git diff --theirs -- <file> 看上游侧改了什么
# 上游仅 version churn → git checkout --ours <file> && git add <file>
# 上游有实质内容 → 手动编辑:保留 fork 品牌字段,合入上游实质内容,然后 git add
```

判断示例:plugin skills 的 fork 侧改动是 version badge 更新(8c5cfb64);若上游侧同文件仅 version 字符串变化,取 `--ours`。

- [ ] **Step 5: CHANGELOG 与 .gitignore**

```bash
cd /tmp/slm-merge-409
git checkout --ours CHANGELOG.md
# 在 CHANGELOG.md 顶部追加一行:
#   ## mslm 4.2.0+upstream — merged upstream SuperLocalMemory 4.0.2–4.0.9 (2026-08-21)
$EDITOR CHANGELOG.md && git add CHANGELOG.md
# .gitignore 若冲突:手动取双方并集后 git add
```

- [ ] **Step 6: 重新生成 uv.lock**

```bash
cd /tmp/slm-merge-409
git checkout --ours pyproject.toml 2>/dev/null || true   # 确保 pyproject 是 fork 版
rm -f uv.lock
uv lock
git add uv.lock
```

Expected: `uv lock` 成功。若报依赖解析错误,说明上游 pyproject 被误取——回到 Step 2 修正。

- [ ] **Step 7: 确认无残留冲突,但暂不提交**

```bash
cd /tmp/slm-merge-409
git diff --name-only --diff-filter=U   # 必须为空
git status | head -5                    # 应显示 "All conflicts fixed but you are still merging"
```

**不要执行 `git commit`** —— Task 3 语义核查与 Task 5 测试通过后才提交(Task 5 Step 4)。

---

### Task 3: fork 补丁存活语义核查

**Files:**
- 只读核查,无改动(发现丢失时才修复)

**Interfaces:**
- Consumes: Task 2 的 merge 状态
- Produces: 核查结果记录(全部 ✅ 才进 Task 4);任何 ❌ 必须当场修复并记录

- [ ] **Step 1: embeddings.py proxy 补丁存活(merge 不应触碰该文件)**

```bash
cd /tmp/slm-merge-409
# 工作树 vs 合并前 HEAD:为空即 merge 未改动该文件(此时 HEAD 仍是合并前的 main)
git diff HEAD --stat -- src/superlocalmemory/core/embeddings.py
grep -n "HF_ENDPOINT" src/superlocalmemory/core/embeddings.py
grep -n "_proxy_http\|_proxy_https" src/superlocalmemory/core/embeddings.py | head -5
grep -n "def __init__" src/superlocalmemory/core/embeddings.py | head -3
```

Expected: 第一条 diff 为空;`HF_ENDPOINT` 移除块与 proxy env 转发块存在;`EmbeddingService.__init__(self, config, proxy_http="", proxy_https="")` 签名存活。

- [ ] **Step 2: engine_wiring.py proxy 透传链路存活**

```bash
cd /tmp/slm-merge-409
grep -n "proxy_http" src/superlocalmemory/core/engine_wiring.py
grep -n "_try_service_embedder" src/superlocalmemory/core/engine_wiring.py | head -5
grep -n "trust_plain_http_lan" src/superlocalmemory/core/engine_wiring.py   # 上游新增,应存在
```

Expected: `_try_service_embedder(cls, emb_cfg, proxy_http=..., proxy_https=...)` 签名与 `init_embedder` 中的 proxy 读取块(config.proxy → _pxy_http/_pxy_https)都在;同时上游的 `trust_plain_http_lan`(init_reranker 内)也合入了——两者在不同函数,应并存。

- [ ] **Step 3: config.py ollama 保留块存活 + 上游新增字段兼容**

```bash
cd /tmp/slm-merge-409
grep -n "Preserve Ollama-specific" src/superlocalmemory/core/config.py
grep -n "ollama_model\|ollama_base_url" src/superlocalmemory/core/config.py | head -6
grep -n "trust_plain_http_lan" src/superlocalmemory/core/config.py
python -m py_compile src/superlocalmemory/core/config.py && echo "config syntax ok"
```

Expected: ollama 保留块(`SLMConfig.load()` 内,约 1222 行区域)存活;上游 `RetrievalConfig.trust_plain_http_lan` 存在;语法编译通过。`EmbeddingConfig` 构造与 dataclass 字段的运行时一致性由 Task 5 全量套件兜底(此阶段 worktree 尚无 venv,不做 import 级检查)。

- [ ] **Step 4: hermes 集成目录未被触碰**

```bash
cd /tmp/slm-merge-409
git diff HEAD --stat -- src/superlocalmemory/integrations/
grep -n "_daemon_api\|daemon_request" src/superlocalmemory/integrations/hermes/__init__.py | head -4
```

Expected: 第一条 diff 为空(目录纯 fork 侧新增,merge 不产改动);daemon-first 路由代码在。

- [ ] **Step 5: hermes 依赖的 daemon 端点形态未变**

```bash
cd /tmp/slm-merge-409
grep -n '@application.get("/recall")' src/superlocalmemory/server/unified_daemon.py
grep -n '@application.post("/remember")' src/superlocalmemory/server/unified_daemon.py
grep -n "include_global\|include_shared" src/superlocalmemory/server/unified_daemon.py | head -6
grep -n "shared_with" src/superlocalmemory/server/unified_daemon.py | head -4
```

Expected: `GET /recall`(约 4159 行)与 `POST /remember`(约 4366 行)存在;`include_global/include_shared` query 参数与 `shared_with`/scope body 字段存活(上游此区间未改这两个 handler 的签名)。

- [ ] **Step 6: `_warm_guard_embed` 新交互确认(不改动,只记录)**

```bash
cd /tmp/slm-merge-409
grep -n "_warm_guard_embed\|_is_remote_embedder" src/superlocalmemory/core/engine.py | head -6
sed -n "$(grep -n '_is_remote_embedder' src/superlocalmemory/core/engine.py | head -1 | cut -d: -f1),+12p" src/superlocalmemory/core/engine.py
```

Expected: 确认 warm-guard 以 `_available is True` + 非 remote 为同步内联嵌入条件。把 `_is_remote_embedder` 的判定逻辑(读到的实际代码)追加记录到 `docs/research-2026-08-21-embeddingservice-daemon-routing.md` §7.1 末尾(方案 B 设计需要它)。

- [ ] **Step 7: NO_CKPT 回归测试对上游版 database.py 仍有效**

```bash
cd /tmp/slm-merge-409
grep -rln "NO_CKPT\|no_ckpt\|ckpt_on_close" tests/
```

Expected: 定位到我方 PR #118 的行为级回归测试文件;逐个运行 `python -m pytest <file> -q`,对上游版 `database.py` 全 PASS。若某测试断言的是我方旧实现细节(如 silent fallback),按上游语义(warn-once)修正断言。

---

### Task 4: 依赖与环境重建

**Files:**
- Modify: 无(pyproject 保持 fork 版;venv 为本地产物)

**Interfaces:**
- Consumes: Task 2 的 uv.lock、Task 3 全 ✅
- Produces: worktree 内可用的新 venv

- [ ] **Step 1: 重建虚拟环境(上一次 merge 的经验:transformers 大版本后必须重建)**

```bash
cd /tmp/slm-merge-409
rm -rf .venv
uv sync --all-extras
```

Expected: 成功。transformers 仍为 5.5.4(上游未动依赖),torch 解析为 >=2.11.0。

- [ ] **Step 2: 验证关键依赖版本**

```bash
cd /tmp/slm-merge-409
uv pip list | grep -iE "^(torch|transformers|sentence-transformers|onnxruntime|sqlite-vec) "
```

Expected: torch>=2.11.0、transformers==5.5.4、onnxruntime==1.24.4、sqlite-vec==0.1.9。

---

### Task 5: 测试门

**Files:**
- 无计划改动;任何失败先按 systematic-debugging 定位再决定是否修

**Interfaces:**
- Consumes: Task 4 的 venv
- Produces: 全绿测试记录;通过后提交 merge commit

- [ ] **Step 1: 全量套件**

```bash
cd /tmp/slm-merge-409
python -m pytest tests/ -q 2>&1 | tail -8
```

Expected: 与基线(`/tmp/slm-merge-409-baseline.txt`)相同的全绿结果。新红的测试按以下优先级判断:上游行为变化导致 fork 适配过期(修测试)→ 冲突解决错误(回 Task 2)→ 上游真 bug(记录,不阻塞,单独上报)。

- [ ] **Step 2: hermes provider 套件(92 项)**

```bash
cd /tmp/slm-merge-409
python -m pytest src/superlocalmemory/integrations/hermes/tests -q 2>&1 | tail -3
```

Expected: 92/92 PASS。conftest 的 daemon 探测隔离确保不打真实 daemon。

- [ ] **Step 3: 版本一致性专项(fork 适配)**

```bash
cd /tmp/slm-merge-409
python -m pytest tests/test_version_consistency.py tests/test_ci_guards/test_release_package_surface.py -q
```

Expected: PASS(pyproject 与 `__init__.py` 都是 fork 4.2.0,无需改动)。

- [ ] **Step 4: gitnexus 变更核查(AGENTS.md 强制)并提交 merge**

```bash
cd /tmp/slm-merge-409
npx gitnexus analyze   # 仅当 gitnexus 提示索引 stale 时执行
# 用 gitnexus MCP 工具 gitnexus_detect_changes() 核查受影响符号/执行流
# (AGENTS.md 强制),确认:
#   - database._connect 的上游演进版替换了我方版本(预期)
#   - 无 fork 独有符号被意外删除
# 若执行环境无 gitnexus MCP,退化为人工核对:
#   git diff main..HEAD --stat | grep -E "embeddings|hermes|engine_wiring" 
#   确认 embeddings.py 零 diff、hermes 目录零 diff、engine_wiring 仅新增上游 hunk
git add -A
git commit -m "merge: upstream SuperLocalMemory 4.0.2-4.0.9 into mslm fork

- database.py: adopt upstream's evolved NO_CKPT fix (supersedes our PR #118
  version; adds per-connection wal_autocheckpoint=400 + warn-once fallback)
- brand layer, pyproject (torch>=2.11.0), versions: keep fork
- verified fork patches survive: embeddings proxy env, engine_wiring proxy
  passthrough, config ollama preservation, hermes daemon-first routing"
```

---

### Task 6: 冒烟门(本地功能实证)

**Files:**
- 无改动

**Interfaces:**
- Consumes: Task 5 提交的 merge commit
- Produces: 冒烟记录;全 ✅ 才允许合回 main

- [ ] **Step 1: doctor 与冷启动 embedding worker(proxy 补丁真实生效)**

```bash
cd /tmp/slm-merge-409
source .venv/bin/activate
slm doctor 2>&1 | tail -15
```

Expected: doctor 通过;embedding worker 冷启动成功(走的是带 proxy env + 无 HF_ENDPOINT 的子进程——若环境中 HF_ENDPOINT 有值而模型加载挂起,说明 Task 3 Step 1 漏检)。

- [ ] **Step 2: daemon 起停 + 基本 recall**

```bash
cd /tmp/slm-merge-409
slm serve start
sleep 5
slm serve status
slm recall "test query" --limit 3
slm serve stop
```

Expected: daemon 正常起停,recall 有结果或空结果但无异常堆栈。注意 4.0.8 引入 backup-before-migrate:首次启动会对测试用数据目录跑迁移并自动打快照——确认迁移日志无 ERROR。

- [ ] **Step 3: 嵌入式宿主召回质量复测(§9.2 联调方法)**

在 daemon 运行中,用一个嵌入式 FULL engine 进程(hermes provider 或手写 5 行脚本)发起 daemon-routed recall:

```bash
cd /tmp/slm-merge-409
slm serve start
python - <<'EOF'
import logging
logging.basicConfig(level=logging.WARNING)
from superlocalmemory.integrations.hermes import _daemon_available, _daemon_api
assert _daemon_available(), "daemon not reachable"
resp = _daemon_api("GET", "/recall?q=embedding&limit=3", timeout=8.0)
assert resp is not None, "daemon recall returned None"
print("daemon-routed recall OK:", str(resp)[:200])
EOF
slm serve stop
```

Expected: 无 embedding-None 警告,daemon 路由返回结果(复刻 4.2.0 联调实证形态:全通道分数,零警告)。

---

### Task 7: 合回 main 与生产升级

**Files:**
- Modify: `docs/upgrade-assessment-2026-08-13-v4-merge-hermes.md`(追加本次 merge 小节)

**Interfaces:**
- Consumes: Task 6 全 ✅
- Produces: main 上的 merge commit;生产升级由用户在场执行

- [ ] **Step 1: 记录 merge 结果到评估文档**

在 `docs/upgrade-assessment-2026-08-13-v4-merge-hermes.md` 末尾追加一节 `## 10. 二次 merge:4.0.2–4.0.9(2026-08-21)`,内容含:merge-base、冲突处置摘要(database.py 取上游演进版等)、存活核查结论、测试/冒烟结果、`_warm_guard_embed` 交互发现。

- [ ] **Step 2: 合回 main**

```bash
cd $HOME/github/superlocalmemory
git checkout main
git merge --ff-only merge/upstream-4.0.9   # 若不能 ff 则普通 merge,先查明 main 是否动过
git worktree remove /tmp/slm-merge-409
git branch -d merge/upstream-4.0.9
```

- [ ] **Step 3: 生产升级(用户在场执行,顺序不可颠倒)**

```bash
# 1. 停 daemon
slm serve stop
# 2. 手动备份数据目录(4.0.8 的自动快照不替代异地备份)
cp -a ~/.superlocalmemory ~/.superlocalmemory.bak-2026-08-21   # 实际路径以 slm 配置为准
# 3. 升级安装
cd $HOME/github/superlocalmemory && uv tool install --force .  # 或既有安装方式
# 4. 启动并观察首启迁移日志(4.0.8 会自动打 pre-migration 快照)
slm serve start && slm serve status
# 5. 生产召回抽查(知惠全通道)+ 观察 24h;备份至少保留一个版本周期
```

Expected: 首启迁移无 ERROR;召回通道完整;24h 内无 WAL/embedding 相关警告洪峰。

---

## Self-Review 记录

- **Spec 覆盖**:上一次 merge 文档 §6/§7 的存活核查项(embeddings proxy env、engine_wiring 透传、config ollama、hermes include_global 行为)→ Task 3 Steps 1–5;§7.1 的 `_warm_guard_embed` 耦合 → Task 3 Step 6;依赖重建(transformers 经验)→ Task 4;生产升级 checklist(备份/24h 观察)→ Task 7 Step 3。
- **冲突策略完备性**:overlap 62 文件中,55 个品牌/plugin churn 由 Task 2 Step 4 的逐文件判断规则覆盖;7 个代码相关文件全部有显式策略。
- **已知不做**:reranker daemon 路由、EmbeddingService 方案 B 实施 —— 均为后续独立工作,不在本 merge 范围。
