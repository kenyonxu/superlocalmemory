# Bounded Loops observation bridge

SuperLocalMemory and Bounded Loops are separate optional products. SLM owns
durable, profile-scoped memory and learning evidence. Bounded Loops owns graph
execution, gate decisions, and receipt truth. Neither package imports the
other, and installing SLM never installs, starts, or configures Bounded Loops.

When both are installed, an MCP client can call
`observe_bounded_loop_evidence(workspace)` to take one explicit read-only
snapshot of terminal graph runs in that workspace. SLM resolves only the
installed `bounded-loops-mcp` executable; callers cannot supply a shell command
or arguments. The workspace must be an existing absolute path.

## Compatibility

The bridge negotiates the producer's advertised MCP capability, not a package
version. It requires this exact contract:

```text
bounded-loops.dev/slm-bridge/v1
```

The producer must advertise `bl_graph_evidence` with the
`observe_terminal_run` operation. SLM then lists terminal runs and fetches each
one with its `run_ref` address. It never sends `run_id` as a replacement for
that address. An absent or incompatible producer returns a structured refusal;
it does not change SLM memory behaviour.

## What SLM stores

Compatible evidence is stored in the additive `learning.db` migration M041,
under the active SLM profile. It preserves the producer's terminal state,
demonstration flag, receipt head, digests, and node gate outcomes. The Living
Brain shows the resulting count as **Bounded Loop observations**.

This is deliberately observation-only. In v1, `eligible_for_learning` must be
false, and SLM does not use these rows for recall, ranking, routing, reward, or
automatic behavioural change. A successful graph run is not proof that a
memory decision should be learned. A future contract can add an explicitly
audited learning authority without changing this v1 boundary.

## Privacy, erasure, and performance

No graph artifacts, commands, paths, or gate prose are copied into SLM. The
bridge reads only the producer's public sanitized evidence document. Evidence
is deleted with its SLM profile and a durable profile-closure tombstone blocks
stale processes from writing it again after erasure.

Observation is an explicit MCP operation and has a five-second producer timeout.
Recall and remember never launch Bounded Loops or open this evidence store;
they never enter its recall or ranking logic. Observation writes use the
bounded learning-db receipt gate and can return a retryable refusal under
contention rather than delaying a memory answer.

## Answer check and loop gates

This bridge is unaffected by [Answer check](answer-check.md) — it observes
Bounded Loops' own graph receipts, not SLM recall. The `slm_loop_run` MCP
tool also verifies an independently executed receipt. Recall supplies context;
its relevance and answerability cannot establish a passing execution.

The MCP watcher requires exactly `gate_contract={"job_id": job["job_id"]}`.
An operator registers the command, its check definition files, and the explicit
mutable artifact paths or globs before execution:

```python
from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check

job = register_job(
    db, profile_id="default", task_id="build", watcher_name="build",
    producer="operator-checker", command=[python_executable, "-m", "pytest", "tests"],
    cwd=project, check_paths=["tests", "pytest.ini"],
    artifact_paths=["src/**/*.py"], data_root=slm_data_root,
    allow_prestart_evidence=False,
)
receipt = run_registered_check(db, job["job_id"], timeout=30)
# The MCP client waits with slm_loop_run(name="build", gate_query="build",
#                                     gate_contract={"job_id": job["job_id"]})
```

Include all tests, scripts, fixtures such as `conftest.py`, and configuration in
`check_paths`. Registration copies these regular files into a content-addressed
store under the SLM data root. Their SHA-256 hashes and the command are pinned;
later live edits to these files do not change the registered check. Definition
files and mutable artifacts must occupy distinct paths. Paths are confined to
the project; symlinks, devices and FIFOs are refused. Explicit directory inputs
include their regular-file trees. Globs are expanded again for each run and
admission, so added or removed matching files change the revision. Other live project files
are not copied into the run directory.

## Gate guarantees and threat model

DONE means that the newest eligible, authenticated receipt reports exit code
zero from the operator's pinned check on the copied, hashed inputs, and that a
fresh copy of the current declared inputs has the same revision. It does not
mean more than that: code the check runs (for example the project's own source
imported by its tests) runs with your user rights, and such code can make its
own check pass, or change another loop's private copy while that loop's check
runs. The gate stops a check's leftover processes and halts when it can see
them, but a process that deliberately detaches and hides is outside what a
same-user tool can stop. When the result matters, keep the check's own code
out of the folder the agent can write, and run one loop at a time.

Receipts record the exit status and hashes of the check's output, never the
output itself, so no form of a secret the check prints can reach a receipt,
the loop history or an MCP reply. To read the output, run the pinned command
yourself. The receipt
binds the job, command, pinned definition, input revision, exit status and output
hashes. Changing declared files requires another check. Changing the check
requires another registration. A newer registration supersedes the previous
job for that profile and watcher. Missing, expired, failed, stale or
unauthenticated evidence cannot produce DONE. By default, receipts must be
produced after the watcher starts; accepting earlier evidence requires the
operator's explicit registration setting.

The check runs with the user's full rights. This gate does not prove that code
executed by the check is honest. In the example above, the pinned tests import
mutable files under `src/`. If an imported file calls `os._exit(0)`, it can end
pytest successfully before assertions run. The gate can then report DONE on
those exact bytes. A signed receipt proves what the check reported; it does
not prove that the tests reached their assertions or that the code is correct.

Checks have network access. A check can take its verdict from a network response.
Those responses, system services and installed tool dependencies are not part
of the copied input manifest. The interpreter and tool installation must be
trusted by the operator. A different same-user process editing SLM's store,
admission key or installed gate code is outside this threat model. The private
copy and file permissions do not prevent a process with the user's rights from
opening a known absolute path elsewhere on the machine.

Each check uses a fresh temporary directory outside the project and the SLM
check store. It receives only the pinned files and declared artifacts, closed
stdin, a restricted environment and private HOME, temp and XDG directories.
A temp setting inside the project or data root is refused. The check runs
without a shell, in its own session and process group, with a bounded timeout. Command paths into the project are relocated into this copy. Check
executables inside the project are refused. Scripts must use relative paths
for their checked inputs; arbitrary embedded paths are not rewritten.

For direct pytest commands and `python -m pytest`, the runner passes
`-c <pinned config>` and `--rootdir <run directory>`. If no config was pinned,
it generates an empty config, passes it with `-c`, and verifies its hash after
the check. An explicit config argument must name a pinned definition file.
`PYTEST_ADDOPTS` and `PYTEST_PLUGINS` are absent from the check environment.
Plugin autoload is disabled; plugins explicitly named by the pinned command
or pinned configuration can still load.

Checks are serialized per data root with an exclusive lock held through
validation and receipt publication. After a check exits or times out, the
runner sends SIGKILL to its process group. It then compares the user's process
table with the baseline captured at check start. It tracks parent chains and
the session and process group IDs recorded at spawn, and gives each check a
private random `SLM_LOOP_RUN_TOKEN` inherited by its children. It reads same-user
process environments when available and attempts to kill surviving descendants
or token holders, including processes that escaped with `setsid` or were
reparented to init or launchd. Observing such a process makes the receipt FAIL
and the watcher HALT, even if termination succeeds. The reason identifies its
name and PID. A process that resists termination can remain alive; the runner
cannot promise that every descendant was stopped. Unrelated processes without
the token or an observed ancestry, session or process group link are ignored.
The lingering-process check is best-effort against code that deliberately scrubs
its environment and leaves the session, consistent with the stated limit that
code executed by the check runs with the user's rights.
An unreadable process table refuses PASS. The runner checks the private input
and definition hashes after this sweep.

Private directories are 0700 and key files are 0600. The per-data-root HMAC key
lives in `loop-admission`, separate from the temporary copies and definition
store. Moving it removes the old run-relative key path; it does not deny
same-user access. An observed key change during execution refuses admission.
Key bytes or their hex, base64 or repr forms in captured output force FAIL.
Receipts store SHA-256 hashes of complete captured output and bounded
excerpts with known key bytes and unbroken key-length material redacted. They
do not retain raw stdout or stderr. The public `execution_receipts.run_check`
writer produces diagnostic receipts without admission; its callback cannot
mint an admitted PASS.

When integrity matters, keep the check's test code outside the agent-writable
workspace before registration, pin configuration and all fixtures, and review
what code the check executes. Keeping tests separate protects their definition;
it does not make imported agent-written code honest. For stronger assurance,
decide the result in a trusted process that code under test cannot control, or
use an operating-system isolation boundary suited to the task. Windows is
refused because the required secure no-follow directory handles are unavailable.
The watcher returns the actual receipt refusal or check failure as its reason,
and history records the last gate detail.

Receipt and registration rows are immutable and erased with their owning
profile. The filesystem definition store belongs to the operator's data root;
profile deletion does not garbage-collect these pinned files. Ordinary `remember`
writes cannot create execution evidence. `require_support` additionally requires
Answer check support for recalled context; support alone cannot establish PASS.
