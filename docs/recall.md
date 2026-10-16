# Recall: Filters, Time Travel and Trace

`slm recall` (alias `slm search`) and the MCP `recall` tool search every
memory in the active profile across several channels — semantic, keyword
(BM25), temporal, associative (Hopfield) and spreading activation — fuse the
results, and rerank them. Then recall decides whether anything it found
actually supports the question, and tells you plainly when nothing does.

## When memory cannot answer

Every memory that recall finds gets one support score from several signals read
together: how far its meaning stands out from what it would match anyway (its
"hub baseline"), the share of your question's words it contains, how the
cross-encoder rates it, whether it carries the rare terms you used, and whether
the question reads as nonsense (words no stored memory contains, or a profile
close to meaningless sentences). The evidence has to come from one memory.
Recall answers when at least one memory clears the threshold, and otherwise
puts the result in one of three tiers:

| Tier | When | What you get |
|---|---|---|
| `supported` | At least one memory is strong enough evidence | The results, as always. A memory below the threshold in the same list is labelled `weak` |
| `related` | Nothing supports it, but the question is a real one and a few memories have real evidence for it | Up to three memories, each labelled `related` and shown as a short preview, under "I don't have this exactly — here are the closest memories." `slm recall` and the dashboard show them by default; over MCP and REST an agent gets them only when it asks with `include_related` |
| `none` | The question reads as nonsense, or nothing is close | No results and the sentence "That isn't in SuperLocalMemory." Pinned memories are still returned |

Without `include_related`, an agent whose question has close memories is told
"I don't have this exactly. Closer memories exist; ask with include_related to
see them." and gets no results. `--show-weak` (`show_weak` over MCP and REST)
returns the closest weak matches instead of nothing, each labelled, with the
same sentence on top. The dashboard's search shows the plain reason instead of
an empty list.

The score is a fitted weighted sum, not a measured probability, and it has
limits. On the development harness it was fitted on (hand-written and generated
questions against a fictional store), the held-out half kept every question that
recall answers (77 of 77) and made nonsense return nothing (152 of 156), but it
caught few questions that sound on-topic and have no answer in memory (7 of 50).
That second case is what the [answer check](answer-check.md) is for. A keyword-only
recall (the model not loaded) is not written off: it serves only the memories that
cover the words you asked with. A question is rewritten into base forms and known
aliases for the keyword search only; the support decision and the cross-encoder
always read your original words. Set `SLM_RECALL_NO_SUPPORT_GATE=1` to turn the
decision off. The code is in `retrieval/support_gate.py`, `retrieval/support_tiers.py`
and `core/score_contract.py`.

## Warming up

For a few minutes after SLM starts, the embedding model, the reranker, the
question probes or the memory graph may still be loading. A recall in that
window cannot judge anything fairly, so it does not claim the memory is empty.
It shows what retrieval found (the top five by keyword), labelled `related`,
under "SuperLocalMemory is still warming up — results may be incomplete. Try
again in a few minutes.", and with nothing found it prints that note instead of
"That isn't in SuperLocalMemory." Over MCP and REST the response carries
`warming_up: true` and the same text in `status_note`; an agent should tell the
person and retry shortly. If the daemon is not running at all, recall says the
service is needed rather than that it is warming up.

## When many agents ask at once

A question you or an agent asks (the MCP `recall` tool, `slm recall`, `GET /recall`,
the Recall Lab, a saved view) always runs the full pipeline. When other agents are
already being answered, it waits for a free slot, up to about twelve seconds. If the
slots are still busy after that, it does not hand back a keyword guess. It answers:

> SuperLocalMemory is answering other questions right now. Ask again in about N
> seconds; nothing was lost.

Over HTTP that is status 503 with a `Retry-After` header and `code: RECALL_BUSY` in
the body. Over MCP the tool returns `code: RECALL_BUSY` and `retryable: true`. The
CLI prints the sentence and exits with code 75. It says nothing about whether the
memory exists; ask again.

Two identical questions asked at the same moment share one run. A complete answer is
reused for a few seconds, and only while nothing is written: any remember, update,
delete or forget drops it at once. Hooks that add memories before your prompt keep a
three-second limit and add nothing when they run out, so typing is never held up.

## Layered recall

How much of each memory a recall returns is your choice, within a budget:

| `depth` (`--depth`) | Returns |
|---|---|
| `0` glance | `fact_id`, `memory_id`, the tier, rank, score and a one-line preview (160 characters). The cheapest; hooks use it |
| `1` recall (default) | The full facts, as recall has always returned them |
| `2` context | Depth 1, plus for each result its source memory, up to three neighbours from the same session, up to three facts about the same entities, and the correction that replaced it, when one did |

`max_tokens` (`--max-tokens N`) caps the whole result list by estimated tokens of
its JSON form and is never exceeded. When it bites, context is dropped first,
then the last memory's text is shortened, then memories are left out, and the
response's `layers` block says what happened (`depth`, `trimmed`, `tokens`,
`left_out`). Depth 2 reads only existing tables, and everything it adds passes
the same checks as `fetch` (profile scope, quarantine, what the caller may see).
An invalid depth is refused (`INVALID_LAYERS` over MCP).

```bash
slm recall "how do we roll back the API" --depth 0                 # ids and previews
slm recall "how do we roll back the API" --depth 2 --max-tokens 1500
```

Over MCP, start at depth 0 or 1 and drill into one result with the `expand` tool:
`expand(memory_id, depth=2, max_tokens=0)` takes a result's `fact_id` or
`memory_id` and returns the full fact and, at depth 2, its surroundings.

## Narrowing a recall

| Flag | Keeps only |
|---|---|
| `--project NAME` | Memories saved under that project |
| `--saved-by AGENT` | Memories saved by that agent (for example `claude-desktop`) |
| `--about NAME` | Memories that mention that person, project or tool |
| `--kind KIND` | One of the nine [memory kinds](memory-kinds.md) |
| `--tag TAG` | Memories saved with that exact tag. Repeat the flag for several tags |
| `--tags-match all\|any` | With several tags: `all` (the default) needs every tag, `any` needs at least one |
| `--window SPAN` | An event-time range: `24h`, `7d`, `30d`, `1y`, or `2026-07-01..2026-07-31` |
| `--limit N` | At most N results (default 20) |

Filters apply on every recall path and before the [answer check](answer-check.md),
so the check judges only what you will see. They combine as AND.

A tag matches by its canonical identity: case, surrounding spaces and how it was
stored never matter. Unlike `--project`, a tag filter never falls back to
unfiltered results. An empty answer says why in `tag_scope`: nothing was ever
saved with that tag, or something has it but none of it matched the question.
Over MCP, `tags` takes a comma-separated string or a list (use the list when a
tag contains a comma).

## Recall inside a project

| Flag (MCP / HTTP parameter) | Effect |
|---|---|
| `--prefer-project NAME` (`prefer_project`) | Memories saved under that project rank above others of similar relevance. Nothing is removed. |
| `--project NAME` (`project`) | Only that project's memories. If none of the memories found were saved under it, you get the unfiltered results and a line saying they are not narrowed. |
| `--project-strict` (`project_strict`) | With `--project`: turn that fall-back off. Only that project's memories, even if there are none, for automations that must never act on another project's memories. |

A project is a name or a path: `acme-billing`, `/Users/me/work/acme-billing`
and `ACME-Billing/` are the same project (the last part of the path, ignoring
case). `session_init` and `slm session open` prefer the project they are given.
The preference is bounded: a project memory passes another memory only when its
score is already at least 80% of that memory's, so a weak match never jumps a
strong one. It is applied after learned ranking (`SLM_RANKING`), on the final
order, so turning learning on does not undo it. The response's `project_scope` says what happened.

A memory carries a project only if one was passed to `remember` when it was
saved. Session-end summaries (`[name] session ended ...`) are tagged with their
project by background maintenance.

## Time travel

| Flag | Returns |
|---|---|
| `--as-of TIME` | Recall pinned to a point-in-time snapshot |
| `--known-as-of TIME` | Only facts SLM knew by that time |
| `--valid-at TIME` | Only facts that were true at that time |

`TIME` is ISO-8601, for example `2026-01-01T00:00:00+00:00`. Facts saved by older releases
have no recorded time; add `--include-unknown` to include them in the
strict `--known-as-of` and `--valid-at` filters. A time that cannot be read
stops the command before any search.

```bash
slm recall "staging database" --known-as-of 2026-01-01T00:00:00+00:00
# No confident match.
```

## Scopes

Recall returns your own (`personal`) memories by default. Add
`--include-shared` or `--include-global` to read memories other profiles
shared, and `--no-shared` or `--no-global` to leave them out when your
configuration includes them. See [Shared memory](shared-memory.md).

## Seeing why a result ranked

```bash
slm trace "how do we roll back the API"
```

`trace` prints the per-channel score breakdown for each result. Its default is
10 results.

## Reading the JSON

`slm recall --json` returns the results plus signals an agent can act on:

| Field | Meaning |
|---|---|
| `tier` | `supported`, `related` or `none`: see [When memory cannot answer](#when-memory-cannot-answer). Empty when recall found no candidates to assess (the sentence in `abstention_note` still says what to tell the person) |
| `support` | `status`, `best`, `threshold`, `version`, `weak_shown` and `tier` of the support decision; `null` when it did not run (context loading, for example) |
| `abstention_note` | The plain sentence for the tier. Show it to the person; do not invent an answer when it says "That isn't in SuperLocalMemory." |
| `warming_up`, `status_note` | True while SLM is still loading, with the sentence to show. Do not conclude the memory is empty |
| `no_confident_match` | Nothing scored high enough to trust |
| `abstained`, `abstention_reason` | No memory supports the question. The reason is `unsupported` (the support decision), `judged_insufficient` (the answer check), `evidence_floor` or `no_candidates` |
| each result's `support`, `weak`, `tier` | That memory's support score, whether it is below the threshold, and its tier label |
| `layers` | With `depth` or `max_tokens`: what was returned and whether it was trimmed |
| `incomplete_channels` | Channels that could not run, for example while the embedding model is still loading. The human output says "Incomplete search" |
| `answer_check_status`, `answer_check_ran` | Whether the answer check ran, and why not when it did not |
| `channel_status` | Per-channel state for this recall: for example `warming` while the embedding model loads, or `no_embedding` |
| `answerability`, `answerability_reason` | `supported`, `unsupported` or `unjudged`. `abstained: false` alone never means the answer check ran; this does. `session_init` always reports `unjudged` |
| `query_id` | Identifies this recall; pass it to `report_outcome` as `recall_query_id` |
| `project_scope`, `tag_scope` | What the project and tag filters did |

Scores are uncalibrated ranking signals, not probabilities. See
[Retrieval score contract](retrieval-score-contract.md).

## Summaries

```bash
slm summary sessions        # sessions that have memories to summarise
slm summary session <id>    # what one session covered
slm summary day             # today's main topics, in this computer's time zone
slm summary project         # what was worked on in a project
```

Every summary says how much of the data it covered and lists the ids of the
memories it was built from. A summary of a session is usually partial, because
most memories carry no session, and it says so. The dashboard has the same
summaries under Memories → Summaries; the MCP tool is `get_memory_summary`.

## Saved views

A saved view is a recall query you run often, saved under a name. Running it is
running recall, so it ranks and checks the same way and gives the same answer on
an unchanged store. Every result shows its memory id. A view belongs to one
profile; no other profile can see or run it. No prompt is ever run over your
memories.

```bash
slm view create "Work log" "what did I ship" --window 7d
slm view list
slm view run "Work log"          # also: slm view show
slm view rename "Work log" "Shipped"
slm view delete "Shipped"        # removes the saved query only
```

Filters: `--kind`, `--window` (`24h`, `7d`, `30d`, `1y` or `2026-07-01..2026-07-31`)
and `--as-of`. Up to 50 results per view (`--limit`, default 10), 200 views per
profile. The dashboard has the same views under Memories → Saved views; the MCP
tools are `run_view` (read) and `manage_view` (write).
