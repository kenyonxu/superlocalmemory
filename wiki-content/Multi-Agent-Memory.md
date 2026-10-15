# Multi-Agent Memory

Several agents can share one SLM install at the same time: Claude Code, Codex, Cursor, Gemini CLI, a Hermes bot and your own scripts all talk to the same daemon and the same memory. Multi-agent memory is what keeps that readable. Every write records which agent made it, the dashboard groups memories by agent, recall can be narrowed to one agent's memories, and trust is tracked per agent. Three other mechanisms coordinate agents beyond attribution: memory scopes that let one profile see another's notes, the SLM Mesh for live messages and locks between sessions, and bounded loops for waiting on a condition. The agent name is a label, not a login. It tells you who claims to have written something; it does not prove it.

## How an agent gets its name

SLM resolves the agent id for each call in a fixed order. First, if the client reached the daemon over HTTP at `/mcp/<agent_id>` (for example `http://127.0.0.1:8765/mcp/claude`), the path segment is the id. Second, for stdio MCP processes, the `SLM_AGENT_ID` environment variable. Third, the placeholder `mcp_client`. The Claude Code plugin sets `SLM_AGENT_ID=claude_code` and the Codex plugin and Codex config template set `SLM_AGENT_ID=codex`; for other clients, set the variable in the client's MCP server `env` block yourself, or connect over `/mcp/<agent_id>`. `remember` and `observe` also accept an explicit `agent_id` argument, which wins over the defaults when you pass one.

Because the id arrives from an untrusted URL segment, it is cleaned in one place before anything uses it: every character outside letters, digits, dot, underscore and hyphen becomes an underscore, and the result is cut to 64 characters. That removes newlines and terminal escape codes (log injection), path separators and oversized values. Two clients can still pick the same name; if you need to know what was allowed to write, the capability each write used is recorded separately and shown as a count per agent in the dashboard.

The same `SLM_AGENT_ID` is the agent's identity on the mesh, so Antigravity, Hermes or Cursor appear as themselves there rather than collapsing into one generic peer. Optimize also uses the agent id to keep each agent's cache and reversible-compression store apart ([Optimize](Optimize)).

## Seeing and filtering by agent

Recall can be limited to one agent's memories with `slm recall "query" --saved-by codex`, or the `saved_by` argument of the MCP `recall` and `search` tools. The match ignores case and surrounding spaces and is a hard filter: if nothing that agent saved matches, you get an empty result rather than other agents' memories. It combines with `project`, `kind`, `tags` and the other facets ([How Recall Works](How-Recall-Works)).

The dashboard's **Multi-Agent Memory** pane shows, for the active profile, how many memories each agent contributed, when it last wrote, which source types it used, and a feed of recent memories. It refreshes about every 15 seconds. It groups by the agent's own name as recorded with the memory, not by the capability digest of the write, because grouping by digest produced rows of unreadable hashes. A writer that never identified itself appears as `unknown` and is flagged as not identified. When no agent has connected yet the pane shows an adapter guide. Related views: Health > Connected agents, and Brain > Connected clients ([Living Brain](Living-Brain), [Health and Repair](Health-and-Repair)). A registry of agents seen is kept in `agents.json` in the data folder.

## Trust per agent

SLM keeps a Bayesian trust score for each agent, a Beta distribution whose mean starts at 0.5. Good behaviour raises it and bad behaviour lowers it: a successful store adds 1 to the positive side, a recall hit 0.5, a rejected store adds 2 to the negative side, a deletion 1.5 and a contradiction 3. After 30 idle days the score drifts back toward the prior at 5 percent a day. The trust gate refuses writes from an agent below 0.3 and deletes below 0.5; reads always pass and are logged.

Names that are really the absence of a name (empty, `unknown`, `unspecified`, `anonymous`, `none`, `null`) are handled on purpose. They always read as the neutral prior and accumulate nothing, because on a real store the `unknown` bucket once collected the combined behaviour of everyone who did not identify themselves and ended up with the highest trust of any agent. Governance > Trust shows the scores, and recall can weight results by trust (Settings > Trust & Source Quality). See [Governance](Governance) for the full model.

## Memory scopes

By default a memory is `personal`: visible only to the profile that wrote it. Two wider scopes exist.

| Scope | Visible to |
|---|---|
| `personal` | The writing profile only (default) |
| `shared` | The profiles named in `shared_with` at write time |
| `global` | Every profile on this install |

```bash
slm remember "Deploy approved for build 2301" --scope shared --shared-with agent-b,agent-c
```

The MCP `remember` tool takes the same `scope` and `shared_with` arguments, with `shared_with` as a comma-separated string. Reading is default-deny: shared and global memories reach a profile only when its settings include them in recall (Settings > Memory Scope has "include shared in recall" and "include global in recall", both off by default), and the default write scope is `personal`. This is an authorization rule inside one install and has nothing to do with the mesh. Profiles are namespaces, not security boundaries ([Profiles](Profiles)).

## The SLM Mesh

The mesh lets live agent sessions on one machine see each other, exchange messages, share small pieces of state and take advisory file locks. The broker runs inside the daemon, so there is nothing separate to install, and it is on by default. Nine MCP tools expose it (`mesh_summary`, `mesh_peers`, `mesh_send`, `mesh_inbox`, `mesh_wait`, `mesh_state`, `mesh_lock`, `mesh_events`, `mesh_status`); they are in the `mesh`, `full` and `power` profiles. The CLI is read-only: `slm mesh status` and `slm mesh peers`. The dashboard has **Mesh Peers** and **Bot messages** panes. Messages from other bots are wrapped as data, never as instructions, and web apps connected through [Web Access and Connected Apps](Web-Access-and-Connected-Apps) show up as peers too. The mesh coordinates peers; it does not copy memory between installs. For bots, peers, trust labels and the loop guard, read [Bots That Talk](Bots-That-Talk).

## Bounded loops

A bounded loop lets one agent wait, within strict iteration and time limits, until an independent check passes. Memory locates the work: agent A saves "review complete: PR #42 is approved", and agent B runs `slm_loop_run` with the gate query "PR #42 approved". Memory alone never completes the loop, though. It ends only when recall finds that locator and an operator-registered check has produced a passing receipt for the current files, a receipt the waiting agent cannot produce itself. Every lap is saved as a memory tagged `loop:<name>`. See [Bounded Loops](Bounded-Loops).

## Choosing a mechanism

| Need | Use |
|---|---|
| One agent reads what another wrote, on the same install | Shared scope, or recall with `--saved-by` |
| An agent waits for a result another agent will save | A bounded loop |
| Live messages, state or locks between running sessions | The mesh |
| Know which agent can be trusted with writes | Governance > Trust |

Code: `src/superlocalmemory/mcp/agent_context.py`, `server/routes/agents.py`, `retrieval/facets.py`, `trust/scorer.py`, `trust/gate.py`, `mesh/broker.py`, `mcp/tools_mesh.py`, `ui/js/od-agents.js`.
