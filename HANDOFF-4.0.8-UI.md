# Handoff — 4.0.8 in flight + dashboard truth audit

## Release state (IMPORTANT — read first)

| | |
|---|---|
| Local `main` | `bbb26d21` — **6 commits ahead of origin**, NOT pushed |
| `origin/main` | `ec083801` (that is 4.0.7) |
| Version in tree | **4.0.8** across all 15 sources |
| Working tree | clean |
| Tag / PyPI / npm for 4.0.8 | **none yet** — nothing published |
| 4.0.7 | fully released: GitHub + tag + PyPI + npm, installed on this machine |

Full suite last run: green at 4.0.7 (9,280 passed). A 4.0.8 run was started and its
result was not captured before compaction — **re-run before publishing.**

The perf gate skips above 50% machine load by design (4.0.7 change); that is not a failure.

## What 4.0.8 already contains (committed, unpublished)

- `slm summary` fixes: one-line highlights, entity **names** not hex ids, Mode B/C
  actually used (the CLI never passed config, so every summary took the Mode A path)
- Code↔memory linking **fixed for real data**: the pass filtered `archive_status IS
  NULL OR = ''` while live facts carry `'live'` — 3,608 of 3,608 excluded. Now
  `COALESCE(archive_status,'live') != 'archived'`
- `get_memory_summary` MCP tool (core profile; counts moved core 16→17, whole 94→95)
- `/api/summary` endpoint + **Summaries tab** in Memories
- Memory drawer: **Code referenced** section, and fixed "Atomic facts" which always
  read "No atomic facts recorded" (it looked up children by the row's own id, but a
  row in that pane IS an atomic fact; parent is a separate `memory_id` field)

## Live data facts established (use these, do not re-derive)

- memory.db: **3,607 facts**, 830 memories, 429 MB, integrity ok, profile `default` (3,603)
- **198 facts have empty `memory_id`** (stored directly — the fact IS the record)
- code_graph.db: **6,533 nodes, 24,358 edges, 576 files, 7,470 links, 1,166 enriched**
  (built manually with fixed code; the INSTALLED 4.0.7 still has the broken filter)
- `~/.superlocalmemory/code_graph_config.json` = `{"enabled":true,"bridge_enabled":true}`
- Mode **B**, Ollama `llama3.2` — confirmed producing `generated_by: llm_b`
- Daemon on 8765 runs under homebrew py3.14 importing from `~/.slm-venv` (4.0.7);
  `slm` CLI is pipx at 4.0.7. `slm serve stop` triggers an auto-restart hook.

## NEXT PHASE — owner's dashboard findings (all unverified; audit each before fixing)

Owner's framing: "these numbers are not getting captured properly". Treat each as a
CLAIM to verify against the DB, not a confirmed bug. Several may be honest empty
states; several are almost certainly real.

### A. Layout (explicit asks)
1. **Recall Lab must be the FIRST tab** in Memories pane (currently last).
2. **Summaries** must be a proper separate tab **next to Recall Lab**.
   Both live in `ui/js/od-memories.js` `_scaffold()` tab strip + `_switchTab()`.

### B. Brain pane — numbers look wrong
3. Overview: "Tasks completed 0 / no tasks recorded yet" after months of use.
4. **Behaviour tab shows 0** and all four cards empty (tech preferences, workflow
   patterns, cross-project transfers, recent outcomes).
5. Reward signal: "Recall quality 0.500 — starting value, no differentiated
   engagement yet" and "All 162 labels carry the default score" despite heavy use.
6. **Connected clients**: "Presence recording gap — last hook event was 9h ago."
   Owner has been using Claude Code continuously.
7. **Source quality**: "18 sources observed … No quality signal has settled yet."

### C. Other panes
8. **Optimize**: Tokens saved **0**, cache hit rate **0%**, compression ratio `—`,
   est. saved $0.0000 — while Master enable, KV Cache and Compression are all ON.
   Check `slm_optimize_stats` counters vs what the pane reads.
9. **MCP & Tools**: shows `core · 16 tools` and `67 total tools` — **stale**. 4.0.8
   makes core 17 and whole 95. Verify where the pane sources these numbers.
10. **Mesh Peers**: completely blank ("No mesh sessions connected"), though mesh has
    its own DB and there is prior peer/last-connected history. Find the mesh DB and
    check whether the pane queries it at all.
11. **Governance → Bounded Loops**: content is old; does not describe the current
    graph engine / bridge / power surfaces.

## Method that kept working this session (keep using it)

- Run tests with **`./.venv/bin/python`** (3.13.5, mcp 2.0.0). System `python3` is
  3.14.5 and gives false failures. This cost hours earlier — do not repeat.
- The daemon **caches static assets at startup**. After editing any UI JS: restart the
  preview server, then assert the SERVED file contains your change before trusting a result.
- Re-stamp content hashes after editing hashed JS
  (`od-brain.js`, `od-graph.js`, `fact-detail.js`, `od-memories.js`) or returning users
  get stale code. Gate: `tests/ui/test_cache_bust_matches_content.mjs`.
- Verify against a COPY of the live DBs
  (`scratchpad/verify408/`), served by the `slm-dashboard-src` launch config on :8801.
- Prove every new gate fails when violated, then restore the file byte-identically.
- "Module imports OK" ≠ "name is bound" — a NameError inside a function only fires when called.
