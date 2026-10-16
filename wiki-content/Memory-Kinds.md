# Memory Kinds

Every memory can say what sort of thing it is: a fact, an event, a decision, a standing rule. Recall uses that to answer "what did we decide" with decisions and "how do I" with how-tos, and the rules and decisions you have confirmed are handed to every new agent session. The central design choice is that a kind guessed by a model is only ever a suggestion. Nothing acts on it until a person or the saving agent confirms it.

## The nine kinds

| Kind | Dashboard label | Use it for |
|---|---|---|
| `semantic` | Fact | "Staging DB is Postgres 16." |
| `episodic` | Event | "The deploy failed on Tuesday." |
| `status` | Current state | "Release 2.3 is in QA." |
| `opinion` | Preference or view | "Alice prefers Terraform." |
| `rule` | Standing rule | "Always run the migration dry-run before a release." |
| `decision` | Decision | "We deploy the API blue-green." |
| `procedure` | How-to | "To rotate the key, run ..." |
| `prospective` | Plan or to-do | "Upgrade the runner next sprint." |
| `correction` | Correction | A fix to something remembered earlier. |

The vocabulary is defined once, in `storage/memory_kinds.py`, and every other module imports it. That is deliberate: several writers must agree on the exact strings and on the order of authority between them.

## Who sets a kind, and how much it is trusted

When a memory is saved, its kind comes from the first of these that applies.

1. The kind the caller declared (`slm remember --kind rule`, or the `kind` argument of the MCP `remember` tool). It is confirmed immediately and applies to every fact extracted from that memory.
2. A later edit by a person.
3. One suggestion from a model that is already running: the Mode B or C extraction call, the Answer Check's on-device Laya worker, or Jev, and Jev only with its own consent (below).
4. Free cue rules, which always run and cost about 2 ms a memory.

The classifier never starts, swaps or warms a model of its own, and a failure in any model falls back to the rules, so saving a memory cannot fail because of a kind. The reason suggestions stay suggestions is measured: on real memories the best model reached 43 percent, which is not good enough to let it decide behaviour. Where a strong cue phrase such as "never push" or "we decided" fired on short text, the cue rules scored 0.887 on the persona test set against 0.48 for the real Laya model, so a model answer that disagrees with a strong cue does not replace it.

### What a person sees

A kind is shown in one of four states, and one setting decides the cut-off, so a memory looks the same in `slm list`, `slm recall`, `slm trace`, the MCP tools, the HTTP API and the dashboard.

| State | Shown as | When |
|---|---|---|
| `confirmed` | The kind, plain. | You set it, or confirmed a suggestion. |
| `suggested` | The kind, marked "(suggested)". | A model's confidence is at least `display_min_confidence`. |
| `legacy` | The nearest kind from the old `fact_type`. | No kind, or confidence below `display_min_confidence`. |
| `untyped` | Nothing. | No kind and no mappable old type. |

`display_min_confidence` defaults to 0.20 and takes 0.0 to 1.0. It is not a CLI flag; change it with `POST /api/memory-kinds/settings` and `{"display_min_confidence": 0.35}`, which needs the same credential as other settings changes.

## What kinds change

**Standing rules at session start.** Confirmed `rule` memories (up to 10) and confirmed `decision` memories (up to 5) are given to every new session through the MCP `session_init` tool. Suggested, rejected, archived, quarantined and replaced items are never included, because a replaced decision is exactly what must not be loaded. Turn this off with `slm kinds settings --standing-rules off`.

**Asking for a kind.** `slm recall "what did we decide about rollback" --kind decision` searches inside that kind. The `kind` filter also exists on `search`, `list_recent` and `slm list`. Without it, questions shaped like "what did we decide", "how do I" or "what is the current state" favour the matching kind (`retrieval.kind_aware`, on by default, boost 0.15). Questions that ask for no kind are ranked as before.

**Replacing an old memory.** `slm remember "Staging DB moved to Postgres 17 on port 5434." --replaces <fact_id>` retires the old memory without deleting it, and recall returns the new one. `review_correction` with action `rollback` undoes it exactly. See [How Remember Works](How-Remember-Works).

## Backends and consent

The backend setting decides which already-running model may suggest kinds for new memories and for the "Classify my memories" run.

| Backend | What types the memories |
|---|---|
| `auto` | The Mode B or C language model if one is available, else the Answer Check's Laya worker if it is running, else the rules. It never resolves to Jev. |
| `rules` | The cue rules only, on this device. |
| `laya` | The on-device model your Answer Check already runs. Falls back to rules if Laya is not the running check. |
| `jev` | Jev, online. Memory text is sent to the Jev service. |
| `llm` | The Mode B or C extraction model, as memories are saved. Existing memories are still typed by rules. |
| `off` | No suggestions at all. |

Hosted typing is the one case that sends memory text off your device, so it has two gates. Jev must be your chosen Answer Check, and `jev_consent` must be the literal boolean `true` in `memory_kinds.json`. A string such as `"true"` or the number 1 counts as no. Set it with `slm kinds settings --backend jev --jev-consent yes`. If either gate is missing, typing quietly uses the rules. A classification run that would send text online is refused with HTTP 409 and the numbers until you confirm it; the CLI confirmation is `--yes`, and the API field is a real boolean `confirm_data_leaves_device`. See [Answer Check](Answer-Check) for the consent model.

These settings live in their own file, `memory_kinds.json`, not in the per-mode copies. The reason is the same one the Answer Check learned: settings kept in `mode_a/b/c.json` are restored on every mode switch, so a consent you withdrew would come back after switching away and back. A damaged file is read as the defaults with no Jev consent.

| Setting | Default |
|---|---|
| `enabled` | `true` |
| `backend` | `auto` |
| `jev_consent` | `false` |
| `display_min_confidence` | `0.20` |
| `standing_rules_in_session` | `true` |
| `llm_extract_kinds` | `true` (the extraction prompt asks the model for a kind; `false` sends the previous prompt unchanged) |

## Classifying existing memories: backfill and undo

"Classify my memories" is a user-started run that can be paused, resumed, cancelled and fully undone. It is rate-limited so it never competes with recall: by default rules run at 500 facts a second, Laya at 8, Jev at 4 and the language model at 3, in batches of 50, 8, 8 and 8. The rules batch is sized so a batch holds the store's write lock for about 50 ms or less, and the runner halves a batch whose write ran over that target. A run never replaces a kind someone confirmed or a higher-authority kind.

```bash
slm kinds backfill start [--mode untyped|refresh] [--yes]
slm kinds backfill status
slm kinds backfill pause RUN_ID     # also resume, cancel
slm kinds backfill revert RUN_ID    # exact undo
```

`untyped` only touches memories with no kind. `refresh` also redoes suggestions, but never a kind you confirmed.

## Command line

| Command | What it does |
|---|---|
| `slm kinds status` | Counts per kind, the backend in use and why, any run in progress. |
| `slm kinds settings [--enable\|--disable] [--backend B] [--jev-consent yes\|no] [--standing-rules on\|off]` | Show or change the settings. |
| `slm kinds review [--kind K] [--limit N]` | Suggestions waiting for confirmation. |
| `slm kinds confirm FACT_ID[=KIND] ...` | Confirm 1 to 200 kinds; a bare id accepts the stored suggestion. |
| `slm kinds set FACT_ID KIND` | Set and confirm one memory's kind. |

These talk to the running daemon, so start it with `slm serve` first. Add `--json` for structured output.

## MCP tools

Four tools mirror the CLI: `set_memory_kind`, `memory_kinds_status`, `review_memory_kinds` and `confirm_memory_kinds` (1 to 200 items per call, one bad item does not stop the rest). They are in the `code`, `full` and `power` tool sets, in `whole` and in the default set, and not in `core` or `mesh`; see [Profiles](Profiles). The base `remember`, `recall`, `search` and `list_recent` tools carry the `kind` argument in every set, and each tool accepts `profile_id`. The full tool list is on [MCP Tools](MCP-Tools).

## In the dashboard

The Memories pane shows a kind badge on each memory and a filter bar of the nine kinds with counts (`GET /api/memories?kind=` and `GET /api/memories/kind-counts`). There is no dedicated Memory Kinds panel: reviewing suggestions, changing the backend, the Jev consent and the backfill run are done from the CLI, the MCP tools or the HTTP routes under `/api/memory-kinds/` (`status`, `settings`, `backfill`, `suggestions`, `confirm`, `fact/{id}`). The routes need MANAGE permission, plus a credential the product issued, even from this machine, for anything that starts a run or changes a setting. See [Dashboard Tour](Dashboard-Tour).

Code: `storage/memory_kinds.py`, `core/memory_kind_config.py`, `encoding/memory_kind_classifier.py`, `core/memory_kind_backfill.py`, `core/memory_kind_backfill_plan.py`, `core/standing_rules.py`, `cli/kinds_cmd.py`, `mcp/tools_kinds.py`, `server/routes/memory_kinds.py`, `retrieval/kind_aware.py`.
