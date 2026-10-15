# Skill Evolution — SuperLocalMemory

> Track, analyze, and evolve your AI agent skills automatically.

---

## What Is Skill Evolution?

Skill Evolution turns SuperLocalMemory from a passive memory system into an active learning engine that tracks how your skills perform and helps them improve over time.

**The problem:** AI agent skills (SKILL.md files, slash commands, agent definitions) are static. A skill installed today works the same way 6 months from now — even if it failed 50 times, even if a better approach was discovered.

**The solution:** SLM observes every skill invocation, builds execution traces, computes performance metrics, and surfaces insights so skills can be improved from real data. When evolution is enabled, SLM can draft an improved version of a skill, verify it blind, and park it in quarantine until you approve it.

---

## How It Works

```
Your session
  │
  │ SLM hook captures every tool call (enriched: input, output, session, project)
  ▼
tool_events table (rich execution data)
  │
  │ Skill performance mining runs during consolidation, for the active workspace
  ▼
Per-skill metrics + behavioral assertions + skill entities
  │
  │ Next session's soft prompts include skill routing recommendations
  ▼
Smarter skill selection, session by session
```

### Three Data Sources

| Source | What It Captures | Cost |
|--------|-----------------|------|
| **SLM Hook** (primary) | Every tool call with input/output (500 chars), session ID, project path. Secret scrubbing built-in. | Zero — runs locally, no LLM calls |
| **ECC Integration** (optional) | Rich observations from [Everything Claude Code](https://github.com/affaan-m/everything-claude-code) via `slm ingest --source ecc` | Zero — reads existing ECC data |
| **Consolidation Pipeline** | Mines tool_events for patterns, creates assertions, updates skill entities | Zero — statistical analysis only |

Consolidation that does this mining runs for the active workspace only. The daemon starts it on a timer, but only after it has been idle for a while, and the session-end hook also asks for a pass. You can run one on demand with `POST /api/v3/consolidation/trigger` (body `{"profile": "<workspace>"}`; it needs manage rights on that workspace). `slm consolidate --cognitive` is a different pipeline and does not mine skills.

### What Gets Tracked Per Skill

| Metric | Description |
|--------|-------------|
| **Invocation count** | How many times the skill was used |
| **Effective score** | Approximate success rate based on execution trace analysis |
| **Session count** | How many sessions used this skill |
| **Skill correlations** | Which skills are frequently used together |

### Outcome Heuristic

SLM uses a conservative, approximate heuristic to determine if a skill invocation was effective:

| Signal | Type | What It Means |
|--------|------|---------------|
| Productive tools follow (Edit, Write, Bash success) | Positive | Skill likely helped |
| Same skill re-invoked within 5 minutes | Negative | Likely retry = failure |
| Bash errors in next 3 tool events | Negative | Something went wrong |
| Session continues 10+ events | Weak positive | User stayed engaged |

These signals are labeled as **approximate** everywhere. They inform soft prompt routing but do not change a live skill without human review.

---

## Dashboard: Skill Evolution Tab

The dedicated **Skill Evolution** tab in the SLM dashboard shows:

- **Overview cards** — Total skill events, unique skills, performance assertions, skill correlations
- **Skill performance cards** — Per-skill effective score, invocation count, confidence level
- **Evolution Engine status** — Backend detection, enable/disable toggle, backend selector, and a button that runs one evolution cycle
- **Skill Lineage DAG** — Visual graph of evolved skill versions (parent → child relationships)
- **Lineage table** — Clickable rows showing evolution type, status, verification result
- **Skill correlations** — Which skills work well together

Access: Open `http://localhost:8765` and navigate to the Skill Evolution tab in the sidebar. Everything the tab shows belongs to the active workspace; switch workspace to see another one's skills. Approving a candidate and rolling one back are done through the HTTP API (see below); the tab does not have buttons for them.

---

## IDE Compatibility

| Client | How tool events reach SLM |
|--------|---------------------------|
| **Claude Code** | The SLM hook is registered by `slm init` (or `slm hooks install`) and posts every tool call |
| **Any other client** | POST to `/api/v3/tool-event` directly |

The backend (API, miner, database, dashboard) does not depend on the client. Any client that POSTs tool events to the `/api/v3/tool-event` endpoint gets the same tracking. The tool-event hook that ships with SLM is written for Claude Code's hook format.

### API Endpoint

```bash
POST http://localhost:8765/api/v3/tool-event
Content-Type: application/json

{
  "tool_name": "Skill",
  "event_type": "complete",
  "input_summary": "{\"skill\": \"my-skill-name\", \"args\": \"...\"}",
  "output_summary": "{\"success\": true}",
  "session_id": "your-session-id",
  "project_path": "/path/to/project"
}
```

All fields except `tool_name` are optional. The input and output summaries are cut to 500 characters. The call needs write permission on the active workspace.

---

## ECC Integration

[Everything Claude Code (ECC)](https://github.com/affaan-m/everything-claude-code) is a popular plugin for Claude Code that provides continuous learning, instinct-based pattern detection, and a rich observation pipeline.

SLM's skill observation patterns were inspired by ECC's architecture. If you have ECC installed, you can enrich SLM's skill tracking with ECC's deeper observations:

```bash
# One-time import of existing ECC observations
slm ingest --source ecc

# Preview without writing (dry run)
slm ingest --source ecc --dry-run
```

This reads ECC's observation files from `~/.claude/homunculus/projects/*/observations.jsonl` and imports them into SLM's `tool_events` table.

**ECC is not required.** SLM is fully self-sufficient — its own hook captures all the data needed for skill tracking. ECC integration is an optional enhancement for users who want both systems working together.

---

## Configuration

### Skill tracking (always on)

Skill performance tracking is enabled by default when the SLM hook is registered. It makes no LLM calls and costs nothing. It runs as one step of full consolidation.

```bash
slm hooks status  # Shows hook registration status
```

### Skill evolution (off by default)

The Skill Evolution Engine uses LLM calls to generate improved skill versions. **It is off by default**; you opt in.

**Why off by default:** Evolution makes LLM calls (a confirmation step, a mutation, and a blind verification). Even with budget caps, you should consciously enable it and choose the backend.

#### Enable via CLI

```bash
slm config set evolution.enabled true
```

#### Enable via Interactive Installer

```bash
slm setup  # Interactive wizard includes evolution opt-in
```

#### Enable via Dashboard

Open Settings, find the Skill Evolution group and enable it (the Skill Evolution tab has the same toggle). Enabling needs owner or administrator rights over every workspace, because the setting applies to the whole installation.

### LLM Backend — Auto-Detect

Evolution uses a single auto-detect chain. No manual configuration needed for most users:

```
Priority 1: `claude` CLI available → spawn `claude --model haiku` (FREE, best quality)
Priority 2: Ollama running         → use Ollama (FREE, local)
Priority 3: API key set            → use Anthropic/OpenAI API (paid)
Priority 4: Nothing available      → dashboard-only (show candidates, manual evolution)
```

This means:
- **Claude Code users:** Evolution works for free — uses your existing Claude subscription
- **Other IDE users with Ollama:** Evolution works for free — uses local Ollama
- **Advanced users:** Can point at Anthropic/OpenAI API if preferred

```bash
# Override auto-detect (optional — most users never need this)
slm config set evolution.backend claude
slm config set evolution.backend ollama
slm config set evolution.backend anthropic
```

### Full Evolution Config Reference

| Key | Default | Description |
|-----|---------|-------------|
| `evolution.enabled` | `false` | Master switch — off by default, opt-in |
| `evolution.backend` | `auto` | LLM backend: `auto`, `claude`, `ollama`, `anthropic`, `openai` |
| `evolution.max_evolutions_per_cycle` | `3` | Budget cap per consolidation cycle (1 to 50 from the dashboard) |
| `evolution.mutation_model` | empty (auto) | Model that drafts the improved skill |
| `evolution.verify_model` | empty (auto) | Model that verifies the draft blind; kept different from the drafting model |
| `evolution.confirm_model` | empty (auto) | Cheap yes/no screening step |

The three model settings accept `auto`, a short alias (`haiku`, `sonnet`, `ollama`, `openai` and a few others), or an allow-listed model id; anything else is refused with the list of accepted values. Local Ollama tags are resolved from the models you have installed, and evolution never pulls a model. With only one model available the verifier cannot be independent, and the status reports that.

### Tracking Thresholds (C1)

| Parameter | Default | Description |
|-----------|---------|-------------|
| MIN_INVOCATIONS | 5 | Minimum uses before creating assertions |
| MIN_CONFIDENCE | 0.5 | Minimum confidence for soft prompt injection |
| TRACE_WINDOW | 10 | Tool events to analyze after each Skill call |
| RETRY_WINDOW | 300s | Same Skill within this window = potential retry |

These are conservative by design: SLM would rather miss a pattern than invent one. They are fixed in the code, not settings.

---

## Research Foundations

SLM's skill evolution system draws from:

- **[EvoSkills](https://arxiv.org/abs/2604.01687)** (HKUDS, 2026) — Co-evolutionary verification with information isolation. +30pp improvement from blind verification.
- **[OpenSpace](https://github.com/HKUDS/OpenSpace)** (HKUDS, MIT) — 3-trigger evolution system (post-analysis + tool degradation + metric monitor). Anti-loop guards. Version DAG model.
- **[SkillsBench](https://arxiv.org/abs/2602.12670)** (2026) — 86-task benchmark showing self-generated skills provide zero benefit without verification. Focused 2-3 module skills outperform exhaustive docs.
- **[SoK: Agent Skills](https://arxiv.org/abs/2602.12430)** (2026) — Four-axis taxonomy. Skills and MCP are orthogonal layers.

---

## MCP Tools

Three MCP tools are available for programmatic access:

| Tool | Description |
|------|-------------|
| `evolve_skill` | Manually trigger evolution for a specific skill |
| `skill_health` | Get health metrics (invocations, error rate, status) for skills |
| `skill_lineage` | Get evolution lineage tree for a skill |

`skill_health` and `skill_lineage` are read-only and take an optional `profile_id` (empty means the active workspace; a named one must exist). `evolve_skill` always works on the active workspace, needs evolution enabled, and writes its candidate into that workspace's own folders (below). These tools are in the `full` and `power` MCP tool sets and in the default set. They are not in `core` or `code`; see [MCP Tools Reference](mcp-tools.md#which-tools-a-client-sees).

## CLI Commands

```bash
slm config get evolution.enabled     # Check if evolution is enabled
slm config set evolution.enabled true  # Enable evolution
slm config set evolution.backend auto  # Set LLM backend
slm config set evolution.mutation_model sonnet  # Pick a model for one step
```

## Workspaces: approvals, rollback and maintenance

Skill evolution is isolated per workspace (profile). Candidates, their history, health numbers, approvals and the daily budget all belong to the workspace that was active when they were made, and another workspace cannot see or approve them: asking to approve a candidate from a different workspace answers "No such evolution candidate". The consolidation pass that mines skill performance and proposes evolutions runs for the active workspace only, so a workspace you are not using is not touched.

A candidate that passes blind verification stops in quarantine and waits for a person. Nothing activates it automatically; approval is explicit, per candidate, and recorded with its history.

```bash
# approve one verified candidate for the active workspace
curl -X POST http://localhost:8765/api/evolution/approve \
  -H 'Content-Type: application/json' \
  -d '{"record_id": "<candidate id>"}'

# put the previous instructions back
curl -X POST http://localhost:8765/api/evolution/rollback \
  -H 'Content-Type: application/json' \
  -d '{"skill_name": "<skill>", "record_id": "<candidate id>"}'
```

If dashboard sign-in is on, these calls need the same credential the dashboard uses. A workspace approval writes the new skill into that workspace's own folder under `workspace_skills/` in the SLM data directory, with its backup and quarantine kept beside it. Approving needs manage rights on that workspace. To write into the shared skills folder (`~/.claude/skills`) instead, send `"installation_wide": true`; that needs the computer's owner or an administrator of every workspace. Rollback restores the prior file and marks the candidate rolled back; the scope it uses is the one recorded when the candidate was activated, and a request that names a different scope is refused. Running one cycle by hand (`POST /api/evolution/run`, or the button on the tab) and changing the evolution settings follow the same rules: a run needs manage rights on the active workspace, and the settings need installation-wide rights.
## Artifacts

Each candidate has its own quarantine directory and a stored content digest. Approval checks those bytes before it makes a backup or activates anything. Installed Codex user, system and plugin skills are discovered alongside Claude skills; a namespaced skill stays within its requested package, and activation and rollback keep the discovered source path. Backup names include a digest of the full skill name, so namespace punctuation cannot make two skills collide.

A one-model installation reports reduced model-review independence. Automatic approval is off; the only way to turn it on is the `SLM_EVO_AUTO_APPROVE` environment variable, which exists for CI. A candidate created before digests were stored needs a `reviewed_hash` (the digest you reviewed) in its approval request.

---

## Links

- [SLM GitHub](https://github.com/qualixar/superlocalmemory)
- [Qualixar](https://qualixar.com)
- [Everything Claude Code](https://github.com/affaan-m/everything-claude-code)
- [OpenSpace](https://github.com/HKUDS/OpenSpace)
- [EvoSkills Paper](https://arxiv.org/abs/2604.01687)
