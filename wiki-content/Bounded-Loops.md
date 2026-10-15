# Bounded Loops

A bounded loop is an agent loop that ends only when an independent check passes, never because the agent says it is finished. SLM ships its own small loop engine and records every lap as ordinary memory, so a run's history survives restarts and shows up in recall and on the dashboard. The name is shared with a separate, optional product, also called Bounded Loops, that runs multi-step graphs; SLM can read that product's finished-run evidence over a published contract but never runs it or lets it influence recall. This page covers both, starting with the one invariant that matters: the gate decides, the agent's claim is only logged.

## The invariant

Each lap the engine runs in a fixed order: check the kill switch, check the budget, let the runner do one lap of work, add up the token spend, ask the gate for a verdict, then decide. The runner may report `agent_claimed_done`. The engine writes that flag to the ledger for audit and never reads it when deciding to stop. The reason is practical: an agent that is wrong about being done is the failure the loop exists to catch, so the stop condition has to come from something the agent cannot assert into existence.

A run ends in exactly one of five terminal statuses.

| Status | Meaning | Reasons you may see |
|---|---|---|
| `DONE` | The gate passed, and approval was granted or not required. The only success state | `gate-passed` |
| `HALT` | A safety bound tripped | `max-iterations`, `token-budget`, `wallclock`, `no-progress` |
| `PAUSE` | The gate passed but required approval was not granted | `awaiting-approval` |
| `KILLED` | The kill switch tripped between laps (set the environment variable `SLM_LOOP_KILL`) | `killed` |
| `ERROR` | The runner or the gate raised before a verdict | the error text |

Report the exact status. A `HALT` is not a success with an excuse attached; it means a bound stopped the run before the gate passed. The token and wall-clock budgets are hard ceilings that win over the gate: a lap that overshoots halts even if its own gate would have passed.

Autonomy is set by a rung. `L1` (report) exits on a passing gate with no approval. `L2` (assisted) and `L3` (unattended) require approval before a passing gate becomes `DONE`, unless the bounds force the posture either way. The MCP and CLI entry points below run at `L1`.

## Waiting for a verified check: `slm_loop_run`

Over MCP, `slm_loop_run` is a watcher. It waits, under hard bounds, until two independent things are true on the same lap. First, recalling `gate_query` returns a memory that is not one of the loop's own ledger rows (a locator: it says where the work is or what the task is), clears the evidence floor, is not judged insufficient by the [answer check](Answer-Check) and scores at least `gate_min_score`. Second, there is an **independent execution receipt**: proof that an operator-registered check ran against the current files and passed. Recall locates context; related text, a high score or a supported answer can never complete the loop by itself, and a receipt produced by the running agent does not count.

This is the part of the design that stops an agent from marking its own homework. The registration and the run of the check are done by the operator, in Python on the SLM computer, not through MCP: no MCP call can register or execute a check. The operator registers a job with `loops.receipt_jobs.register_job`: the command as an argument list (no shell), the working folder, the check files to pin (tests, scripts, fixtures such as `conftest.py`, configuration) and the mutable artifacts the agent is allowed to change, as paths or globs. Registration copies the check files into a content-addressed store under the data folder and pins their hashes and the command, so later edits to those files change nothing. The operator then runs `run_registered_check`, which executes only that pinned definition in a fresh private directory and records a receipt. The agent calls `slm_loop_run(name=..., gate_query=..., gate_contract={"job_id": "<the registered job id>"})`. The contract must be exactly that one key, a 32-character hex id.

```python
from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check

job = register_job(
    db, profile_id="default", task_id="build", watcher_name="build",
    producer="operator-checker", command=[python_executable, "-m", "pytest", "tests"],
    cwd=project, check_paths=["tests", "pytest.ini"],
    artifact_paths=["src/**/*.py"], data_root=slm_data_root,
)
receipt = run_registered_check(db, job["job_id"], timeout=30)

# the agent waits:
# slm_loop_run(name="build", gate_query="build status",
#              gate_contract={"job_id": job["job_id"]})
```

A receipt counts only when all of these hold: it was produced by someone other than the running agent; it was produced after the watcher started (unless the operator allowed earlier evidence at registration); it is authentic and its output hashes match; it reports exit code 0 for a pass; the job has not expired (a registration lives for at most a day, one hour by default) or been replaced by a newer registration for the same profile and watcher; and a fresh copy of the declared artifacts has the same revision as the one the check ran on. If the files changed, the gate says to run the registered check again. Without a valid `gate_contract` the loop cannot reach `DONE`: it runs to its bound and halts with the reason, such as "operator-registered job_id required", "no execution receipt" or "check FAILED (exit 1)". The reply's `reason` names the refusal rather than only the bound that tripped. On a host that cannot open directories safely without following links, registration is refused and the reason says so.

**What DONE means, and what it does not.** DONE means the newest eligible, authenticated receipt reports exit code zero from the operator's pinned check on the copied and hashed inputs, and that the current declared inputs still match. It does not mean the code is correct. The check runs with your user's rights, so code it imports can make the check pass (a file that exits the process successfully before the tests assert anything, for example), or change another loop's private copy; the check has network access and its verdict can depend on responses SLM does not copy; and a process that deliberately detaches and hides is beyond what a tool running as the same user can stop. SLM stops a check's leftover processes and halts when it can see them. For results that matter, keep the check's own code out of the folder the agent can write, and run one loop at a time. Receipts record the exit status and hashes of the check's output, never the output, so nothing the check prints can reach a receipt, the loop history or an MCP reply; to read the output, run the pinned command yourself.

The call blocks, polling the gate, until the gate passes or a bound trips, then returns `ok`, `status`, `reason`, `bound_reason`, `receipt_reason`, `passed`, `laps`, `run_id`, a short ledger and a note that independent receipts decide completion. On internal failure it returns `ok: false` with a message instead of raising.

| Parameter | Default | Limit |
|---|---|---|
| `name` | required | 1 to 128 characters; also the memory tag |
| `gate_query` | required | 1 to 2,000 characters |
| `gate_contract` | none | `{"job_id": ...}` from an operator registration; required for `DONE` |
| `gate_min_score` | 0.0 | Minimum top-result score (0.0 means any confident hit above the evidence floor) |
| `max_iterations` | 20 | 1 to 200 |
| `max_wallclock_s` | 15.0 | Capped at 120; 0 removes the wall-clock limit |
| `poll_interval_s` | 1.0 | At least 0.25 |
| `max_tokens` | 0 | 0 means no token budget |
| `no_progress_window` | 0 | Leave at 0 for a pure watcher, which never "changes" and would halt before the gate can pass |
| `require_support` | false | Also require the answer check to have run and judged the memories sufficient |

Each lap does one recall of at most three memories and asks the answer check once, never for a reorder; with the online check that is a request to the provider each lap. The loop's own ledger rows are excluded from the recall gate, because they are stored as memories and would otherwise match the query on the next lap.

`slm_loop_history(name, limit, profile_id)` lists recorded runs (`run_id`, lap count, final decision, time), and `slm_loop_show(run_id, limit, profile_id)` returns every lap with its decision, pass flag, detail, the recorded `agent_claimed_done` and the tokens. Both are read-only. The three tools are in the `code`, `full` and `power` MCP profiles ([MCP Tools](MCP-Tools)).

## From the command line and from hosts

```bash
slm loop demo                      # keyless demo; proves the engine and ledger end to end
slm loop demo --iterations 10 --json
slm loop history                   # runs for the default demo loop name
slm loop history --name my-loop --json
slm loop show <run_id>
```

The CLI is for inspection and the demo only; the demo uses a built-in stub runner. To run a real loop from a host, use the `/slm-loop` command and the `slm-loop` skill in the Claude Code and Codex plugins, which hand the work to the `slm-loop-runner` agent. For a task with a checkable condition such as a test suite, a schema or a linter, the agent runs that command itself each lap; SLM does not execute commands for the agent. A loop whose completion must be independently proven uses the registered-check route above. The runner agent reports the exact terminal status and never dresses up a non-`DONE` result.

## The ledger

Every lap is written to SLM memory with the tag `loop:<name>`, a run id, the lap number, the decision (`continue`, `done`, `halt`, `pause`, `killed`, `error`), the gate's one-line detail and a budget snapshot. Because it is ordinary memory it is queryable with `slm recall`, appears in the dashboard, and moves with your backups. Ledger writes fail open: a write error is counted and reported with the run instead of aborting a loop in flight, since the gate is the correctness guarantee and the ledger is observability.

## The separate Bounded Loops product

The graph runner Bounded Loops is a different package with its own gates and receipts. The two never import each other, and installing SLM never installs or starts it. When both are present, an MCP client can call `observe_bounded_loop_evidence(workspace)` to take one explicit, read-only snapshot of finished graph runs in an absolute workspace path. SLM finds only the installed `bounded-loops-mcp` executable (callers cannot supply a command), checks that it advertises the exact contract `bounded-loops.dev/slm-bridge/v1`, and stores compatible evidence under the active profile in the learning database. The call has a five-second producer timeout.

This is observation only. The field `eligible_for_learning` must be false, and the rows are not used for recall, ranking, routing, reward or any behaviour change, because a graph run succeeding is not proof that a memory decision should be learned. Recall and remember never launch Bounded Loops or open this store. No graph artifacts, commands, paths or gate prose are copied; only the producer's public, sanitised evidence document is read, and the evidence is deleted with its profile. A second tool, `observe_bounded_loop_execution_learning(workspace)`, ingests terminal evidence from the current bridge contract into an execution-reliability store that never writes semantic facts or preferences; it is not in any named profile and is available under `whole`. The Living Brain shows the count as Bounded Loop observations ([Living Brain](Living-Brain)).

## Dashboard

Integrations > Bounded Loops has four parts: Bridge status, How it works, a "Run it" block with the CLI command and MCP call, and Framework adapters. Detection of the separate product uses a PATH lookup for `bounded-loops-mcp`, then a version call with a 1.5-second timeout, and it does not claim the bridge is live. If it is absent the pane says "not installed", which is a normal, complete state. The framework list shows the nine adapters in [Framework Adapters](Framework-Adapters). For coordination between agents without a loop see [Multi-Agent Memory](Multi-Agent-Memory).

Code: `src/superlocalmemory/loops/` (`engine.py`, `models.py`, `rules.py`, `budget.py`, `ledger.py`), `loops/receipt_jobs.py`, `loops/receipt_gate.py`, `loops/execution_receipts.py`, `mcp/loop_runner.py`, `mcp/tools_loops.py`, `cli/loop_cmd.py`, `integrations/bounded_loops_mcp.py`, `mcp/tools_brain.py`, `ui/js/od-boundedloops.js`.
