[中文](./multi-scope-memory-zh.md) | [English](./multi-scope-memory-en.md)

# MSLM Multi-Scope Memory

> MSLM's core feature: flexible memory isolation and sharing through a three-tier scope model (personal / shared / global).
> Multiple AI Agents maintain their own private memories while sharing team knowledge and global entities.

---

## 1. Three-Tier Scope Model

MSLM introduces an independent `scope` dimension, orthogonal to user profiles (`profile_id`):

```
┌──────────────────────────────────────────────────────┐
│  Global                                              │
│  Shared technical entities: React, Python, Docker... │
│  New entities default to this layer                  │
│  RRF weight: 0.5                                     │
├──────────────────────────────────────────────────────┤
│  Shared                                              │
│  Memories shared with specific Agents                │
│  Target Agents specified via shared_with parameter   │
│  RRF weight: 0.7                                     │
├──────────────────────────────────────────────────────┤
│  Personal                                            │
│  Private memories visible only to the current Agent  │
│  Highest RRF fusion weight (1.0)                     │
└──────────────────────────────────────────────────────┘
```

**Design principles**:
- `profile_id` = User identity (tech stack, preferences, work habits)
- `scope` = Visibility boundary (personal / shared / global)
- Orthogonal combination: a single profile can have personal, shared, and global memories

**Retrieval priority**: personal > shared > global. During RRF fusion, personal memories rank highest and global memories rank lowest. Weights can be customized in `config.json` → `scope_weights`.

---

## 2. Cross-Scope Retrieval

When a user queries, MSLM automatically searches all three scopes in parallel, then merges and ranks results via RRF (Reciprocal Rank Fusion):

```
query ──┬──► personal scope (profile_id isolation)
        │    └── Returns only the current user's personal memories
        │
        ├──► shared scope (shared_with matching)
        │    └── Returns memories shared with the current Agent
        │
        └──► global scope (no profile restriction)
             └── Returns globally visible memories

              ↓
        [Weighted RRF Fusion] ──► Cross-scope ranking ──► Return Top-K
```

**Default weights** (customizable in `config.json`):

| Scope | Default Weight | Description |
|-------|:-------------:|-------------|
| `personal` | 1.0 | Highest priority, personal memories first |
| `shared` | 0.7 | Shared memories second |
| `global` | 0.5 | Global memories lowest weight |

---

## 3. Global Canonical Entities

All Agents share a single set of technical entities (React, Python, Kubernetes, etc.) — no duplicate entity copies.

- New entities **default to global scope**
- Any Agent can create global entities
- All Agents' `recall` automatically includes memories linked to global entities
- Entity aliases and fuzzy matching support cross-scope lookup

### Domain Tags

MSLM automatically maps entities to technical domains (frontend / backend / devops / mobile / data) for cross-Agent domain matching:

- **Rule engine**: 48 built-in entity→domain mappings (React→frontend, Docker→devops, etc.)
- **LLM fallback**: Entities not matching any rule are classified by LLM and cached
- Domain tags enable `shared` scope cross-Agent matching — Agents with overlapping domains automatically share relevant memories

---

## 4. Using with MCP Tools

### Storing Memories with Scope

**remember tool parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | required | Content to store |
| `scope` | string | `"personal"` | Scope: `personal` / `global` / `shared` |
| `shared_with` | string | `""` | Only valid when `scope="shared"`, comma-separated Agent IDs |
| `tags` | string | `""` | Comma-separated tags |
| `importance` | int | `5` | Importance score (1-10) |
| `session_id` | string | `""` | Session ID for grouping related memories |

**Examples**:

```
# Personal memory
Call remember, store "User prefers TypeScript + Tailwind", scope = "personal"

# Global knowledge
Call remember, store "Team convention: TypeScript strict mode", scope = "global"

# Shared with specific agents
Call remember, store "Backend API spec v2", scope = "shared", shared_with = "backend_agent,frontend_agent"
```

### Controlling Retrieval Scope

**recall tool parameters**:

| Parameter | Description | Default |
|-----------|-------------|:-------:|
| `query` | Query text (required) | — |
| `limit` | Number of results | `10` |
| `include_global` | Include global scope memories | `true` |
| `include_shared` | Include shared scope memories | `true` |

> **Note**: In the current version, `include_global` / `include_shared` parameters are accepted but all three scopes are always included in results. Fine-grained per-scope filtering will be enabled in a future release.

### CLI Commands

```bash
# Store memories with different scopes
mslm remember "Personal preference: functional programming" --scope personal
mslm remember "React 18 supports Concurrent Features" --scope global
mslm remember "Internal API key rotation rules" --scope shared --shared-with "backend_agent,frontend_agent"

# List entities by scope
mslm entity list --scope personal
mslm entity list --scope shared
mslm entity list --scope global

# Merge duplicate entities
mslm entity merge <source_id> <target_id>
```

### Hermes MemoryProvider

With the Hermes MemoryProvider plugin, scope is controlled via tool parameters:

```python
# Store memories with different scopes
slm_remember("Personal preference: prefer functional programming", scope="personal")
slm_remember("React 18 supports concurrent features", scope="global")
slm_remember("Internal API key rotation policy", scope="shared", shared_with="backend_agent")

# Control scope visibility during recall
slm_recall("React concurrent features", include_global=True, include_shared=False)
```

> See [Hermes Agent Integration Guide](hermes-agent-guide-en.md#5-multi-scope-memory) for details.

---

## 5. Multi-Agent Collaboration Examples

### Scenario 1: Global Knowledge Sharing

Agent A (zhihui) stores a memory about React. Agent B (xiaoming) retrieves it automatically.

```
# Agent A stores global knowledge
Call remember: store "React 18 supports Concurrent Features", scope = "global"

# Agent B retrieves
Call recall: query "What are React's new features"
→ Returns Agent A's "React 18 supports Concurrent Features"
```

### Scenario 2: Targeted Sharing of Sensitive Information

Agent A only wants to share sensitive information with specific Agents.

```
# Agent A stores shared memory
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

### Scenario 3: Multi-Agent Bootstrap

Pre-load shared knowledge into global scope when multiple Hermes Agents come online:

```python
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.config import SLMConfig

TEAM_KNOWLEDGE = [
    "Project Phoenix uses React 18 + TypeScript + Tailwind CSS",
    "API gateway deployed on Kong, backend in Go microservices",
    "Database: PostgreSQL 16, ORM: Prisma",
    "CI/CD: GitHub Actions, deploy to AWS EKS",
    "Repo: monorepo managed with Turborepo",
    "Coding standards: ESLint + Prettier, strict mode",
]

def bootstrap_team_knowledge(profile_id: str = "default"):
    config = SLMConfig(active_profile=profile_id)
    engine = MemoryEngine(config=config)
    engine.initialize()

    for knowledge in TEAM_KNOWLEDGE:
        engine.store(
            content=knowledge,
            scope="global",
            metadata={"tags": "team,convention", "imported_from": "bootstrap"},
        )

# Initialize for each Agent
for agent in ["zhihui", "xiaoming", "xiaohong"]:
    bootstrap_team_knowledge(agent)
```

---

## 6. Profile vs. Scope

| Dimension | Meaning | Purpose |
|-----------|---------|---------|
| `profile_id` | Whose memory | Identity isolation (different users/projects) |
| `scope` | Visibility boundary | Tier isolation (personal/shared/global) |

Orthogonal combination:
- A single profile can have personal, shared, and global memories
- Different profiles' personal memories are fully isolated
- Different profiles share the same global entity space

```bash
# Manage profiles
mslm profile list              # List all profiles
mslm profile create work       # Create a work profile
mslm profile switch work       # Switch to work profile
```

In Hermes Agent, specify via `session_init`:

```
Call session_init, profile_id = "work"
```

---

## 7. Key Features Summary

- **Global entity sharing**: Technical entities like React and Python are created once, shared by all Agents
- **Automatic cross-scope retrieval**: RRF fusion automatically merges results from all three scopes
- **Flexible visibility**: personal (private), shared (targeted sharing), global (visible to all)
- **Domain tag matching**: 48 built-in entity→domain mappings with LLM fallback
- **Backward compatible**: Memories without explicit scope default to `personal`
- **Adjustable weights**: personal/shared/global RRF fusion weights customizable in config.json

---

## References

- [Getting Started](getting-started-en.md) — From installation to daily use
- [Hermes Agent Integration Guide](hermes-agent-guide-en.md) — MCP protocol integration
- [Memory Import Guide](memory-import-guide-en.md) — Bulk import to three-tier scope
- [Configuration Guide](configuration-en.md) — scope_weights and other settings

---

*MSLM (Multi-Scope Local Memory) — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
