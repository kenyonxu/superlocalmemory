# cron 提示词泄漏修复实施计划（sync_turn 道＋第四条道＋存量清理）

> 2026-10-21 ｜ 起因：知惠自检报告「SLM 守卫未生效：门装对了，样板从另一扇门进来」，
> 复核确认诊断属实并做了三处修订（见 §2）。本计划按修订版整理。

---

## 1. 诊断结论（复核确认版）

**漏点**:`sync_turn`(`src/superlocalmemory/integrations/hermes/__init__.py` 821 行起）。
hermes 每轮结束调 provider 的 `sync_turn(user, assistant)`,850 行拼成
`User: {clean_user}\nHermes: {clean_asst}` 入库。cron 会话的"用户消息"就是调度器注入的
`[IMPORTANT: You are running as a scheduled cron job.…]` 提示词块，于是每轮一条。

**守卫覆盖现状**:`_is_cron_hint`(144 行，双条件：固定开场白＋内部不变标记）只挂在
`on_memory_write`(895 行）与 `on_pre_compress`(933 行）。全文件枚举写入调用点共
**四条道**（知惠数了三条）:

| 道 | 位置 | 守卫 |
|---|---|---|
| `sync_turn`（每轮自动） | 858/863 行 | ❌ 漏 |
| `on_memory_write`（内建记忆镜像） | 901/906 行 | ✅ |
| `on_pre_compress`（压缩镜像） | 946/951 行 | ✅ |
| `_tool_remember`(`slm_remember` 工具，agent 主动写） | 1181/1188 行 | ❌ 漏 |

**生产实证**(`~/.superlocalmemory/memory.db` 只读查询）:

- cron 样板条数：10/19 落 47、10/20 落 53、10/21 截至午间 9 条；存量合计 **421 条**
  (9/22 起。9/25–10/19 的空档正是 gateway 缺包断连 11 天遮住了这条道，10/19 包less
  修复恢复连通后泄漏随之恢复——这条道的泄漏史比守卫本身老）。
- 内容形态逐字对上 sync_turn 拼接模板：`User: [IMPORTANT: …` 开头，样本长度一致
  (watchdog 固定模板）。
- 写入元数据 `{"speaker": "user", "source": "hermes-provider"}` 与 `_engine_store`
  (708 行）的打点完全一致；session_id 为 `cron_<hash>_<date>_<time>` 格式。

**为什么必须在内容层守**:`initialize` 的会话级 cron 跳过（533–536 行，
`agent_context in {"cron","flush"}` → `_cron_skipped`）即使被 hermes 正确触发也挡不住
——`_can_serve()`(453–462 行）的契约是 daemon 可达即服务（"the daemon-first route
serves them instead"),`sync_turn` 照样经 daemon 落库。内容级守卫是唯一可靠闸层；
也排除了"让 hermes 传对会话标记"的替代路线（那条路还会顺带掐掉 cron 任务的召回能力）。

**部署拓扑（本次复核新发现，计划与运维笔记都需要它）**：知惠 gateway 进程
(`gateway run`,HERMES_HOME=`~/.hermes/profiles/zhihui`）的 provider 加载自
`$HERMES_HOME/plugins/superlocalmemory/`，该目录下 `__init__.py` / `plugin.yaml`
是**指向本仓库工作树的符号链接**（10/19 包less 修复时建立）。推论：

- 工作树里这个文件的任何改动，**gateway 重启即生效**（模块重载；`__pycache__`
  按 mtime 自动重编译，无需手工清）。daemon 代码不动，本次不需要重启 daemon。
- 反向警示：该文件的未提交本地改动等价于"已部署待生效"——改完必须当次走完
  提交＋重启，不要半成品留在工作树。

---

## 2. 对知惠工单的三处修订

1. **"剥离样板段只留正文"是错选项，必须"命中直接整轮不写"。** 生产样本里 hint 后面的
   "正文"是静态任务模板（"你是知惠的系统健康 watchdog。脚本已经运行了 health_check.py……"
   ＋ "Hermes: OK")。剥离 hint 只留正文，每天照样落 54 条，只是更短——漏水量不变。
   cron 运行里真正有价值的内容（早报、晚报正文）应走 `on_memory_write`/`slm_remember`
   主动写道，那些道只挡 hint 本身、正文照过，不受影响。
2. **测试路径修正**：守卫测试文件已存在，随 ec6218aa 提交于
   `src/superlocalmemory/integrations/hermes/tests/test_cron_hint_guard.py`
   （不是顶层 `tests/`)。在其中按现有 `_SyncThread` 模式补用例即可。
3. **工单漏项**：第四条道 `_tool_remember` 同样无守卫（暂无观测泄漏，但"hint 是传输
   不是记忆"的不变式应在所有道成立）；以及 **421 条存量垃圾没有清理方案**——守卫上线
   只止血，存量不清会继续污染召回（正是 CJK 误杀案里 shares_token 垃圾那一类）。

`on_session_end`(965–971 行）已复查：只调 `close_session`，无写入道，无需动。

---

## 3. 任务分解

### Task 1 — `sync_turn` 守卫（整轮丢弃）

文件：`src/superlocalmemory/integrations/hermes/__init__.py`

在 843–844 行（`clean_user`/`clean_asst` 计算）之后、噪声过滤之前插入：

```python
        if _is_cron_hint(clean_user):
            # The cron scheduler hint is transport, not memory. The whole turn
            # is a machine run — the task template riding below the hint is
            # static boilerplate, and real cron output reaches the store
            # through on_memory_write / slm_remember instead.
            logger.debug("MSLM sync_turn dropped cron scheduler hint turn")
            return
```

行为契约：命中即整轮不落库（不剥离、不留正文）。判定只看 `clean_user`——hint 由
调度器以用户消息注入，与观测形态 100% 对齐；双条件匹配保证用户引用片段不误杀
（已有单测钉住）。

### Task 2 — `_tool_remember` 守卫（第四条道）

同文件，1166 行（content 空检查）之后插入：

```python
        if _is_cron_hint(content):
            return json.dumps(
                {"status": "filtered",
                 "message": "cron scheduler hint is transport, not memory — not stored."},
                ensure_ascii=False,
            )
```

返回形状取 `filtered` 状态 JSON（对齐 1197 行 noop 的返回风格）：不是 error
（不是故障），也不是静默成功（否则 agent 以为已记住）。

### Task 3 — 测试补齐

文件：`src/superlocalmemory/integrations/hermes/tests/test_cron_hint_guard.py`

- `_SyncThread` 助手补 `is_alive() -> False`（支持同一 provider 实例多次调
  `sync_turn`)。
- `TestSyncTurnGuard`:
  1. hint 轮（`_CRON_HINT`＋任务模板，assistant 回 "OK")→ `_engine_store` 未被调；
  2. 真实轮（"今天去了九间房。"/"记下缺口坐标。")→ 恰好写一次，且保持
     `User: …\nHermes: …` 格式；
  3. hint＋**真实 assistant 回复** → 仍整轮丢弃（钉住"整轮"契约）;
  4. 用户消息只引用开场白片段 → 照写（防误杀回归）。
- `TestToolRememberGuard`:
  1. hint 内容 → 不写，返回 `status == "filtered"`;
  2. 真实内容 → 照写。

测试中一律 patch `_can_serve` 与 `_engine_store`——本机 daemon 在线，不 patch 会
打到真 daemon。

测试命令（本仓库无 .venv,house 标准）:

```bash
env -u ALL_PROXY -u all_proxy PYTHONPATH=src ~/miniconda3/bin/python \
  -m pytest src/superlocalmemory/integrations/hermes/tests/test_cron_hint_guard.py -v
```

另跑同目录全量测试做回归。已知基线：卫生测试 1 红为上游 v2-archive 文档遗留
(merge 基线既有，与本次无关），不因此卡关。

### Task 4 — 存量清理 421 条（生产操作，设确认点）

走 daemon 正规删除路径，**禁止直接 SQL 改写**(daemon 拥有唯一写路径；10/19 教训：
手工软删导致派生层每 90 秒失败一次）。

1. 基线（只读）:SQL 统计 profile zhihui 下 cron 样板数并记录；
2. dry-run 预审：`slm --profile zhihui forget "You are running as a scheduled cron job" --dry-run --json`
   ——matched_count 与基线对齐、抽查样本内容全为 cron 样板后才放行；
3. 执行：`slm --profile zhihui forget "You are running as a scheduled cron job" --yes`
   (daemon `DELETE /api/memories/<id>` 逐条，派生层随正规路径一致清理）;
4. 验证：SQL 计数归零；`atomic_facts` 里 cron 样板派生事实同步归零。

时机：安排在 Task 5 的 gateway 停用窗口内（写入方停机，清理期间不会有新样板落入）。

### Task 5 — 部署与验证（生产操作，设确认点）

部署 = 提交合入工作树 ＋ 重启 gateway（符号链接拓扑，daemon 不动）。

1. 代码合入 main、推送；
2. 【确认点】按既有流程重启 hermes gateway（知惠服务有短暂中断窗口；
   重启期间执行 Task 4 清理）;
3. 验证项：
   - gateway 日志无 MSLM provider 加载错误；
   - 连续观察两个 watchdog 周期（每小时 :15/:45)→ cron 样板 SQL 计数零新增；
   - 阳性对照：一次真实交互轮正常落库（防误杀把真内容也挡了）;
   - cron 任务召回能力不受影响（知惠的 cron 作业仍能 recall——内容级守卫只挡写）。

### Task 6 — 记录回写

- 运维笔记新增 §30：问题现象、根因（四条道守两条＋symlink 部署拓扑）、修复内容、
  清理数字、验证结果；
- 更新记忆文件 `project-context-superlocalmemory-fork.md`：补部署拓扑事实
  （profile 插件目录符号链接→工作树，改动即待部署）;
- `docs/上游贡献清单.md` 记一笔：fork 侧 provider 守卫链补全，作为 hermes provider
  upstreaming 的论据（不单独开 PR)。

---

## 4. 风险与回滚

| 风险 | 评估与对策 |
|---|---|
| 误杀真实内容 | `_is_cron_hint` 双条件（固定开场白＋内部标记）,`sync_turn` 只查 user 侧；Task 3 用例 4 钉住 |
| cron 有价值输出丢失 | 有意取舍：sync_turn 里的 cron 轮正文是静态模板；真价值走主动写道（不受影响） |
| 清理误删 | dry-run 先审，matched_count 必须对齐基线；逐条 DELETE 可审计 |
| 部署故障 | 回滚 = `git revert` ＋ gateway 重启，分钟级 |
| 字节码陈旧 | `__pycache__` 按 mtime 自动失效，无需手工清 |

## 5. 执行模式建议

体量小（两处守卫＋一个测试文件＋一次清理＋一次重启），建议**直接执行**，不走
subagent-driven 全流程。Task 1–3 一次提交；Task 4–5 同一个维护窗口；Task 6 收尾。
