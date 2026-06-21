[中文](./hermes-agent-guide-zh.md) | [English](./hermes-agent-guide-en.md)

# MSLM × Hermes Agent Integration Guide

> This guide is for [Hermes Agent](https://github.com/3rdparty/hermes) users who want to integrate MSLM via the MCP protocol for persistent multi-scope memory.

---

## Prerequisites

- Python 3.11+
- Hermes Agent installed with MCP protocol support
- OS: macOS / Linux / Windows

---

## 1. Install MSLM

```bash
pip install mslm-memory
```

> `pip install superlocalmemory` also works. CLI commands `mslm` and `slm` are equivalent.

Verify installation:
```bash
mslm --version
mslm doctor
```

---

## 2. First-Time Setup

```bash
mslm setup
```

The setup wizard guides you through mode selection:

| Mode | Name | Description | Recommended For |
|------|------|-------------|-----------------|
| **A** | Local Guardian | Zero-cloud, zero-LLM, fully local | Privacy-first, air-gapped |
| **B** | Smart Local | Uses local Ollama LLM | Data stays on LAN |
| **C** | Full Power | Uses cloud LLM | Maximum retrieval quality |

**Mode A is recommended for Hermes Agent users** — works immediately with no extra dependencies.

---

## 3. Connecting Hermes Agent to MSLM

MSLM exposes its services through the MCP protocol. Start command:

```bash
mslm mcp
```

### 3.1 MCP Configuration

Add MSLM to your Hermes Agent MCP configuration file.

**Config file location** (depends on Hermes version):
- `~/.config/hermes/mcp.json`
- Or the MCP config page in Hermes Agent settings

**Configuration template**:

```json
{
  "mcpServers": {
    "mslm": {
      "command": "mslm",
      "args": ["mcp"],
      "env": {
        "SLM_MODE": "a",
        "SLM_MCP_ALL_TOOLS": "0"
      }
    }
  }
}
```

### 3.2 Environment Variables

| Variable | Description | Recommended |
|----------|-------------|-------------|
| `SLM_MODE` | Operating mode: `a` / `b` / `c` | `a` |
| `SLM_MCP_ALL_TOOLS` | `1` to enable all 75 tools, `0` for 33 core tools | `0` |
| `SLM_MCP_MESH_TOOLS` | `1` to enable 8 Mesh P2P tools | As needed |

> **About the data directory**: MSLM stores all data at `~/.superlocalmemory/` by default. If you need a custom path, ensure MCP and daemon (`mslm serve start`) use the **same** `SLM_DATA_DIR` environment variable — otherwise memories will be written to two separate databases. The simplest approach is to leave `SLM_DATA_DIR` unset and use the default path.

> **Tip**: If Hermes Agent limits the number of tools, keep `SLM_MCP_ALL_TOOLS=0` to expose only the 33 core tools.

### 3.3 Verify Connection

Test in Hermes Agent:

```
Call remember to store: "I'm using Hermes Agent"
```

Then test retrieval:

```
Call recall to search: "What agent am I using"
```

Correct results indicate successful integration.

---

## 4. Core Tools Quick Reference

Once MSLM is registered with Hermes Agent, the following tools are available:

### Memory Operations

| Tool | Function | Example |
|------|----------|---------|
| `remember` | Store memory | "Remember project Phoenix uses React 18" |
| `recall` | Semantic retrieval | "What's the Phoenix project tech stack" |
| `search` | Search memories | "Search all memories about databases" |
| `fetch` | Get by ID | "Get memory ID abc123" |
| `list_recent` | List recent memories | "Show last 10 memories" |
| `delete_memory` | Delete memory | "Delete memory abc123" |
| `update_memory` | Update memory | "Update memory abc123 content" |

**remember tool parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | required | Content to store |
| `tags` | string | `""` | Comma-separated tags |
| `scope` | string | `"personal"` | Scope: `personal` / `global` / `shared` |
| `shared_with` | string | `""` | Only valid for `scope="shared"`, comma-separated Agent IDs |
| `importance` | int | `5` | Importance score (1-10) |
| `session_id` | string | `""` | Session ID for grouping related memories |

### Sessions & Context

| Tool | Function |
|------|----------|
| `session_init` | Initialize a new session |
| `observe` | Have MSLM observe current context |
| `report_feedback` | Report memory usefulness |
| `get_status` | Get system status |

### Entity Management

| Tool | Function |
|------|----------|
| `merge_entities` | Merge duplicate entities |

### Usage Examples in Hermes Agent

Send natural language instructions:

> Call remember to store: "User prefers TypeScript + Tailwind, project codename Phoenix, deployed on Vercel"

> Call remember to store: "Team API spec v2", scope = "shared", shared_with = "backend_agent,frontend_agent"

> Call recall: "Where is the Phoenix project deployed"

> Call observe on this code: [paste code snippet]

---

## 5. Multi-Scope Memory

MSLM introduces a three-tier scope architecture, allowing multiple Agents in a Hermes team to maintain private memory spaces while sharing public knowledge.

### 5.1 Three-Tier Scope Model

```
┌──────────────────────────────────────────────────────┐
│  Global                                              │
│  Shared technical entities: React, Python, Docker... │
│  New entities default to global scope                │
│  RRF weight: 0.5 (adjustable via config.json)        │
├──────────────────────────────────────────────────────┤
│  Shared                                              │
│  Memories shared with specific Agents                │
│  Target Agents specified via shared_with parameter   │
│  RRF weight: 0.7 (adjustable via config.json)        │
├──────────────────────────────────────────────────────┤
│  Personal                                            │
│  Private memories visible only to current Agent      │
│  Highest RRF fusion weight (1.0, adjustable)         │
└──────────────────────────────────────────────────────┘
```

**Retrieval priority**: personal > shared > global. During RRF fusion, personal memories rank first and global memories last. Weights can be customized in `config.json` → `scope_weights` (default: personal=1.0, shared=0.7, global=0.5).

### 5.2 Global Canonical Entities

All Agents share the same set of technical entities (React, Python, Kubernetes, etc.) — no duplicate entity copies.

- New entities **default to global scope**
- Any Agent can create global entities
- All Agents' `recall` automatically includes memories linked to global entities
- Entity aliases and fuzzy matching support cross-scope lookup

### 5.3 Domain Tags

MSLM automatically maps entities to technical domains (frontend / backend / devops / mobile / data) for cross-Agent domain matching:

- Rule engine: 48 built-in entity→domain mappings (React→frontend, Docker→devops, etc.)
- LLM fallback: unmatched entities are classified by LLM and cached
- Domain tags enable cross-Agent matching for `shared` scope — Agents with overlapping domains automatically share relevant memories

### 5.4 Using in Hermes Agent

**Store memories with scope**:

```
Call remember, store "Project Phoenix uses React 18", scope = "personal"
```

```
Call remember, store "Team convention: TypeScript strict mode", scope = "global"
```

```
Call remember, store "Backend API spec v2", scope = "shared", shared_with = "backend_agent,frontend_agent"
```

| `scope` value | Meaning | Visibility |
| --- | --- | --- |
| `personal` | Self only | Current profile_id |
| `global` | Everyone | All Agents |
| `shared` + `shared_with` | Targeted sharing | profile_id + listed Agents |

**Control retrieval scope**:

```
Call recall, query "React tech stack", include_global = true
```

| Parameter | Description | Default |
| --- | --- | --- |
| `include_global` | Include global scope memories | `true` |
| `include_shared` | Include shared scope memories | `true` |

> **Note**: In the current version, `include_global` / `include_shared` parameters are accepted but all three scopes are always included in results. Fine-grained per-scope filtering will be enabled in a future release.

### 5.5 Multi-Agent Collaboration Examples

**Scenario**: Agent A (zhihui) stores a memory about React. Agent B (xiaoming) retrieves it automatically.

```
# Agent A stores global knowledge
Call remember: store "React 18 supports Concurrent Features", scope = "global"

# Agent B retrieves (automatically finds Agent A's global memory)
Call recall: query "What are React's new features"
→ Returns Agent A's "React 18 supports Concurrent Features"
```

**Scenario**: Agent A only wants to share sensitive info with specific Agents.

```
# Agent A stores shared memory (only backend_agent and frontend_agent can see it)
Call remember: store "Internal API key rotation: every 30 days"
  scope = "shared"
  shared_with = "backend_agent,frontend_agent"

# Agent B (backend_agent) retrieves
Call recall: query "API key rotation"
→ Returns "Internal API key rotation: every 30 days"

# Agent C (devops_agent) retrieves
Call recall: query "API key rotation"
→ No results (not in shared_with list)
```

**Key features**:
- Global entities (e.g., React) are created once, shared by all Agents with the same entity ID
- Knowledge graph edges, domain tags linked through global entities are also auto-shared
- No manual sync needed — RRF fusion auto-merges results from all three scopes on retrieval

---

## 6. User Profiles

MSLM supports multi-profile isolation for switching contexts in Hermes Agent:

```bash
# CLI management
mslm profile list              # List all profiles
mslm profile create work       # Create work profile
mslm profile switch work       # Switch to work profile
```

In Hermes Agent, specify via `session_init`:

```
Call session_init, profile_id = "work"
```

**Profile vs. Scope**:
- `profile_id` = whose memory (identity isolation)
- `scope` = visibility boundary (tier isolation)
- Orthogonal: a single profile can have personal/global/shared memories

---

## 7. Background Daemon (Recommended)

Start the daemon to eliminate cold-start latency:

```bash
mslm serve start     # Run in background
mslm serve status    # Check status
mslm serve stop      # Stop
```

Or one-click restart:
```bash
mslm restart         # Kill orphans + cleanup + restart + health check
```

---

## 8. Web Dashboard

```bash
mslm dashboard       # Opens http://localhost:8765
```

The dashboard provides:
- Memory network graph visualization
- Retrieval stats and performance
- System health checks
- Memory maintenance (consolidation, decay, quantization)

---

## 9. Daily CLI Quick Reference

```bash
# Most common
mslm remember "content" --tags "tag1,tag2"
mslm remember "content" --scope global               # Store in global scope
mslm remember "content" --scope shared --shared-with "agent1,agent2"  # Shared
mslm recall "query" --limit 10
mslm list -n 20
mslm status

# Maintenance
mslm consolidate --cognitive   # Cognitive consolidation (dedup)
mslm decay --execute           # Forgetting curve decay
mslm quantize --execute        # Embedding quantization (space saving)

# Entity management
mslm entity list --scope personal
mslm entity list --scope shared
mslm entity list --scope global
mslm entity merge <source_id> <target_id>

# Diagnostics
mslm doctor
mslm health
```

---

## 10. Troubleshooting

| Symptom | Steps |
|---------|-------|
| Slow cold start | Run `mslm serve start` first to start the daemon |
| Poor retrieval quality | Run `mslm health` to check Fisher-Rao / Sheaf status |
| Tool call failure | Check `mslm doctor` output, verify MCP server is running |
| Data directory conflict | MCP and daemon must use the same `SLM_DATA_DIR`; leave unset for default `~/.superlocalmemory/` |

---

## References

- [Multi-Scope Memory](multi-scope-memory-en.md) — Three-tier scope core concepts
- [Getting Started](getting-started-en.md) — From installation to daily use
- [Configuration Guide](configuration-en.md) — Operating modes and parameters
- [SLM Technical Docs](slm/INDEX.md) — Upstream technical reference

---

*MSLM (Multi-Scope Local Memory) — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
