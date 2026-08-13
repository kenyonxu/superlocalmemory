# Postmortem: SQLite Global VFS Mutex Deadlock via WAL Close (2026-08-13)

> **Status**: mitigated (services restarted); root cause NOT fixed — recurrence risk remains
> **Scope**: superlocalmemory ↔ any host process embedding it (hermes-agent gateway)
> **Author**: incident analysis with py-spy native stack evidence

## TL;DR

A superlocalmemory background thread (`mslm-sync`) blocked inside
`sqlite3Close → sqlite3WalClose → unixLock` while closing a WAL-mode
connection, waiting on a file lock pinned by the long-lived
`unified_daemon` process. The close path holds SQLite's **process-global
VFS mutex**, so every subsequent `sqlite3.connect()` in the host process
queued on `findReusableFd → pthread_mutex_lock` — silently freezing the
hermes gateway's delivery worker and cron scheduler while its event loop,
health endpoint, and messaging sockets stayed green. A user reply was
generated but never delivered (`delivery_obligations.state='attempting'`,
`attempts=0`, 20+ minutes).

## Symptom timeline (2026-08-13, CST)

| Time | Event |
|------|-------|
| 12:36:02 | User message #1 arrives (Discord); turn completes, reply delivered at 12:36:13 |
| 12:42:36 | User message #2 arrives; session guard stale → gateway sends "⚡ Interrupting current task..." ack |
| 12:42:42 | Reply generated successfully (`messages.finish_reason=stop`); slm recall runs ("Query embedding returned None" warning) |
| 12:42:43 | `delivery_obligations` row created, state `attempting` — **never progresses** |
| 12:43:14 | "Discord Slash command sync timed out" (side effect of API interaction backing up) |
| 12:42–13:03 | Gateway looks healthy: `/health` 200, websocket connected, event loop in `select()` — but every new `sqlite3.connect()` blocks forever |
| 13:03 | Gateway restarted; new process adopts the orphaned obligation (owner_pid dead) and delivers the reply at 13:03:15 |

## Root cause chain

```
unified_daemon (15d 17h uptime) holds WAL reader marks on
~/.superlocalmemory/learning.db (and recall_queue.db)
        │
        ▼
host process thread "mslm-sync" finishes a store pipeline and closes its
WAL connection → sqlite3WalClose must checkpoint → unixLock waits for the
daemon-pinned lock — WHILE HOLDING the process-global VFS mutex
        │
        ▼
every later sqlite3.connect() in the host process stacks up in
findReusableFd → pthread_mutex_lock on that same global mutex:
  - delivery worker   (gateway/delivery_ledger.py:81, mark_delivered)
  - cron-scheduler    (cron/executions.py:29, create_execution)
  - mslm-prefetch     (superlocalmemory/storage/database.py:166)
        │
        ▼
host event loop needs no new DB connection → /health stays 200 →
failure is invisible to liveness checks
```

## Evidence (py-spy native dump, host PID 2925199)

Lock waiter — close path (the mutex holder that started the cascade):

```
Thread 2936288 (idle): "mslm-sync"
    pthread_mutex_lock (libc.so.6)
    unixLock (libsqlite3.so.3.51.1)
    sqlite3WalClose (libsqlite3.so.3.51.1)
    sqlite3PagerClose.isra.0 / sqlite3BtreeClose.isra.0
    sqlite3LeaveMutexAndCloseZombie / sqlite3Close
    connection_close (pysqlite connection.c:473)
    execute (superlocalmemory/storage/database.py:237)
    _node_degree (superlocalmemory/encoding/graph_builder.py:159)
    run_store (superlocalmemory/core/store_pipeline.py:410)
```

Blocked openers — two of three shown:

```
Thread 2925673 (idle): "asyncio_1"  (delivery worker)
    pthread_mutex_lock → findReusableFd → unixOpen → sqlite3BtreeOpen
    → openDatabase → pysqlite_connection_init (connection.c:261)
    _connect (gateway/delivery_ledger.py:81) → mark_delivered

Thread 2925713 (idle): "cron-scheduler"
    (identical C frames) → _connect (cron/executions.py:29)
    → create_execution → tick
```

Cross-checks at the time of the hang:

- External process `sqlite3.connect(..., timeout=3)` to the same files:
  **succeeds instantly** → not a filesystem/OS-level lock issue.
- No `-journal` files; no POSIX records for the waiters in `/proc/locks`
  (futex wait is userspace).
- Both stuck threads: `wchan = futex_do_wait`.
- Full dumps preserved at: `/tmp/gateway-stack-1242.txt` (Python) and
  `/tmp/gateway-stack-native.txt` (native) — volatile, key frames inlined
  above.

## Mechanism notes

1. **`findReusableFd` serialization** (libsqlite3 3.51.1, unix VFS):
   `unixOpen` takes a process-global mutex to scan the reusable-fd list.
   Any thread that blocks mid-close while holding VFS-mutex lineage turns
   every later `openDatabase` into a process-wide convoy.
2. **WAL close requires a checkpoint**, which requires locks that a
   long-lived second process (the daemon) can pin indefinitely. With a
   15-day reader the close path blocked 20+ minutes without timing out.
3. **DELETE-mode close needs no checkpoint** — this exact convoy cannot
   form on the close path (a hot-journal rollback can still wait on locks,
   but only when a crash left one behind, which is rare and recoverable).

## Why WAL is hard-coded here (and why that's a problem)

At least 10 call sites force `PRAGMA journal_mode=WAL` with no config
switch, e.g. `storage/database.py:140` (`_enable_wal()`),
`server/unified_daemon.py:542` ("Enforce WAL mode for concurrent reads"),
`server/unified_daemon.py:637`, `storage/schema.py:68`,
`mesh/broker.py:113`, `cli/pending_store.py:63`,
`server/routes/tiers.py:39`, `code_graph/database.py:70`,
`core/graph_pruner.py:49`, `core/backend_orchestrator.py:298`.

WAL is load-bearing for the daemon + embedded-host + MCP-subprocess
concurrent-read design, so a blanket DELETE switch is an architecture
decision, not a config tweak. On 2026-08-13 all 7 DBs were manually
switched to DELETE; the daemon forced 6 of them back to WAL on startup
(confirms the pragma is unconditional).

## Remediation options

| Option | Change | Kills this deadlock? | Trade-offs |
|--------|--------|----------------------|------------|
| A. DELETE everywhere | rewrite ~10 pragmas (add `journal_mode` config knob, default DELETE or WAL) | Yes (close path needs no checkpoint) | Read-during-write blocks under DELETE; upstream merges; low load here so likely unnoticeable |
| B. Keep WAL, fix close semantics | passive-checkpoint-before-close + short/0 busy_timeout on the close path; or `SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE` | Yes (close no longer waits) | Deepest fix, most code care; uncheckpointed WAL grows between restarts |
| C. Operational backstop | restart unified_daemon on a daily timer to drop ancient WAL pins; host watchdog on `attempting` obligations older than N minutes | Reduces probability, does not eliminate | Zero code change; recurrence possible |
| D. Host-side isolation (hermes) | hermes already removed its own WAL switching (2026-08-13 fix log); the residual exposure is inherent to embedding slm in-process | No | — |

Recommended: **A** (aligns with the host's post-incident policy and slm's
low write concurrency) or **B** if WAL concurrency is genuinely needed;
**C** is worth doing immediately regardless.

## Immediate recovery procedure (when it recurs)

1. Confirm: `SELECT state, attempts FROM delivery_obligations ...` shows
   `attempting` with `attempts=0` and stale `updated_at`.
2. Optional forensics (do this BEFORE restart or the evidence is gone):
   `sudo py-spy dump --native --pid <gateway_pid>` — look for
   `findReusableFd` waiters plus one `sqlite3WalClose/unixLock` holder.
3. `systemctl --user restart hermes-gateway.service` — the new process
   adopts orphaned `attempting` obligations (dead `owner_pid`) and
   re-sends; verified 13:03:15.
4. If the daemon has weeks of uptime, restart it too
   (`kill <pid>`; it does not auto-respawn its child more than once —
   relaunch with
   `setsid nohup python3.13 -m superlocalmemory.server.unified_daemon --start --port=8765 &`).

## Related

- hermes-agent post-merge fix log (same day, different failure class —
  journal-mode switch contention):
  `hermes-agent/docs/plans/2026-08-13-post-merge-fix-log.md`
- SQLite WAL checkpoint/reader pinning: https://sqlite.org/wal.html
