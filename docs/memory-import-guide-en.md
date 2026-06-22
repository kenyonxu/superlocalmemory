[中文](./memory-import-guide-zh.md) | [English](./memory-import-guide-en.md)

# MSLM Memory Import Guide: Three-Tier Scope Bulk Import

> This guide covers bulk-importing memories from external memory services (other Agent memories, knowledge bases, note systems) into MSLM, properly distributed across the three-tier scope (personal / shared / global).

---

## Pre-Import Preparation

### 1. Format Your Data

Organize external memories into a unified JSON format:

```json
{
  "content": "Memory content text",
  "scope": "personal|global|shared",
  "shared_with": ["agent_id_1", "agent_id_2"],
  "tags": "tag1,tag2",
  "agent_id": "Source Agent identifier",
  "imported_from": "Source system name"
}
```

**Field descriptions**:

| Field | Required | Description |
| --- | --- | --- |
| `content` | Yes | Memory content — MSLM auto-extracts atomic facts |
| `scope` | No | Scope, defaults to `personal` |
| `shared_with` | No | Agent ID list when `scope=shared` |
| `tags` | No | Comma-separated tags |
| `agent_id` | No | Source Agent ID for provenance |
| `imported_from` | No | Source system name (e.g., `mem0`, `langchain_memory`) |

### 2. Determine Scope Assignment Strategy

Decide which tier each memory belongs to before importing:

| Memory Type | Recommended Scope | Reason |
| --- | --- | --- |
| General technical knowledge (React, Docker usage) | `global` | Shared by all Agents, avoid duplication |
| Team conventions, project standards | `global` | Team consensus, visible to all |
| Domain-specific collaborative knowledge | `shared` | Only visible to relevant Agents |
| Personal preferences, private project info | `personal` | Self only |

---

## Method 4: MCP Tool Calls

Best for runtime dynamic import by Agents. **Full three-tier scope support.**

### Core Tools

Agents call the following tools via the MCP protocol:

| Tool | Purpose | Scope Support |
| --- | --- | --- |
| `remember` | Store memory | `scope` + `shared_with` parameters |
| `recall` | Retrieve memory | `include_global` + `include_shared` flags |
| `entity list` | View entities | `--scope` filter |
| `entity merge` | Merge entities | Auto-handles cross-scope conflicts |

### remember Parameters

```python
remember(
    content: str,           # Memory content (required)
    tags: str = "",         # Comma-separated tags
    scope: str = "personal",  # personal | global | shared
    shared_with: str = "",  # When scope=shared, comma-separated agent_id list
    agent_id: str = "mcp_client",  # Source Agent ID
    importance: int = 5,    # Importance 1-10
)
```

### recall Parameters

```python
recall(
    query: str,             # Query text (required)
    limit: int = 10,        # Number of results
    include_global: bool = True,   # Include global scope
    include_shared: bool = True,   # Include shared scope
)
```

> **Note**: In the current version, `include_global` / `include_shared` parameters are accepted but all three scopes are always included. Fine-grained per-scope filtering will be enabled in a future release.

### Batch Import Example

```python
import json

memories = json.load(open("exported_memories.json"))

for mem in memories:
    scope = mem.get("scope", "personal")
    shared_with = ",".join(mem.get("shared_with", []))

    # Via MCP call
    mcp_call("mslm", "remember", {
        "content": mem["content"],
        "tags": mem.get("tags", ""),
        "scope": scope,
        "shared_with": shared_with,
        "agent_id": "import_batch",
    })

    time.sleep(0.1)  # Avoid pending queue overload
```

---

## Method 2: Python Script Bulk Import

Best for large offline imports with precise per-memory scope control.

### Basic Script

```python
#!/usr/bin/env python3
"""Bulk-import memories into MSLM with three-tier scope assignment."""

import json
import sys
import time

from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.config import SLMConfig


def import_memories(data_file: str, profile_id: str = "default") -> None:
    """Bulk-import memories from a JSON file.

    Args:
        data_file: Path to JSON file containing a memory array.
        profile_id: Target profile.
    """
    with open(data_file) as f:
        data = json.load(f)

    memories = data if isinstance(data, list) else data.get("memories", [])
    if not memories:
        print("No memory data found")
        return

    config = SLMConfig(active_profile=profile_id)
    engine = MemoryEngine(config=config)
    engine.initialize()

    imported = 0
    errors = 0

    for i, mem in enumerate(memories):
        content = mem.get("content", "").strip()
        if not content:
            continue

        scope = mem.get("scope", "personal")
        shared_with = mem.get("shared_with")
        if isinstance(shared_with, str):
            shared_with = [s.strip() for s in shared_with.split(",") if s.strip()]

        try:
            fact_ids = engine.store(
                content=content,
                scope=scope,
                shared_with=shared_with,
                metadata={
                    "tags": mem.get("tags", ""),
                    "agent_id": mem.get("agent_id", "import"),
                    "imported_from": mem.get("imported_from", "external"),
                },
            )
            imported += 1
            if (i + 1) % 50 == 0:
                print(f"  Imported {i + 1}/{len(memories)}...")
        except Exception as e:
            errors += 1
            print(f"  Import failed [{i}]: {e}")

    print(f"\nImport complete: {imported} succeeded, {errors} failed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_memories.py <memories.json> [profile_id]")
        sys.exit(1)
    import_memories(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "default")
```

### Run

```bash
python import_memories.py exported_memories.json zhihui
```

---

## Method 3: Dashboard API Import

For bulk import via HTTP. **Does not support scope parameters** (defaults to personal).

### Start Dashboard

```bash
mslm dashboard    # http://localhost:8765
```

### Prepare JSON File

```json
{
  "memories": [
    {
      "content": "Team convention: all API responses use snake_case",
      "tags": "api,convention"
    },
    {
      "content": "Kubernetes HPA auto-scales based on CPU usage",
      "tags": "k8s,devops"
    }
  ]
}
```

### Call Import API

```bash
curl -X POST http://localhost:8765/api/import \
  -F "file=@memories.json"
```

> **Limitation**: Dashboard `/api/import` only supports `content`, `tags`, `project_name`, and `category` fields.
> It does not support `scope`/`shared_with`. For three-tier scope, use Method 1 (MemoryProvider) or Method 3 (Python script).

---

## Method 1: Hermes MemoryProvider Import (Recommended)

With the Hermes native plugin, you can import memories directly in a Hermes session — no MCP setup needed:

```python
# Batch import (call within Hermes session)
memories = [
    {"content": "Team convention: use snake_case for all API responses", "scope": "global"},
    {"content": "Personal preference: use pnpm over npm", "scope": "personal"},
    {"content": "Project key rotation policy", "scope": "shared", "shared_with": "backend_agent"},
]
for m in memories:
    slm_remember(m["content"], scope=m.get("scope", "personal"),
                 shared_with=m.get("shared_with", ""))
```

> MemoryProvider supports full `scope`/`shared_with` parameters — the simplest way to bulk-import three-tier scope memories.
> See [Hermes Agent Integration Guide](hermes-agent-guide-en.md)

---

## Common Import Scenarios

### Scenario 1: Migrate from Mem0 / LangChain Memory

```python
#!/usr/bin/env python3
"""Convert Mem0 export format and import into MSLM."""

import json
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.config import SLMConfig

def import_from_mem0(mem0_export_file: str, profile_id: str = "default"):
    """Mem0 export format is typically [{"id": ..., "memory": "content", "metadata": {...}}]"""
    with open(mem0_export_file) as f:
        mem0_data = json.load(f)

    config = SLMConfig(active_profile=profile_id)
    engine = MemoryEngine(config=config)
    engine.initialize()

    for item in mem0_data:
        content = item.get("memory", "")
        if not content:
            continue

        # Mem0 memories are usually general — assign to global
        engine.store(
            content=content,
            scope="global",
            metadata={
                "tags": ",".join(item.get("metadata", {}).get("tags", [])),
                "imported_from": "mem0",
                "original_id": item.get("id", ""),
            },
        )

    print(f"Imported {len(mem0_data)} memories from Mem0")

if __name__ == "__main__":
    import_from_mem0(sys.argv[1])
```

### Scenario 2: Import from Markdown Notes

```python
#!/usr/bin/env python3
"""Import notes from Markdown files into MSLM."""

import re
from pathlib import Path
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.config import SLMConfig

def import_markdown_notes(
    notes_dir: str,
    scope: str = "personal",
    profile_id: str = "default",
):
    """Scan all .md files in a directory, split by paragraph, and import."""
    config = SLMConfig(active_profile=profile_id)
    engine = MemoryEngine(config=config)
    engine.initialize()

    md_files = list(Path(notes_dir).glob("**/*.md"))
    total = 0

    for md_file in md_files:
        text = md_file.read_text()

        # Strip YAML frontmatter
        text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL)

        # Split by paragraph (consecutive blank lines)
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]

        for para in paragraphs:
            if len(para) < 20:  # Skip fragments too short
                continue
            engine.store(
                content=para,
                scope=scope,
                metadata={
                    "tags": "",
                    "imported_from": "markdown",
                    "source_file": str(md_file.relative_to(notes_dir)),
                },
            )
            total += 1

    print(f"Imported {total} memories from {len(md_files)} Markdown files")
```

### Scenario 3: Multi-Agent Bootstrap

Pre-load shared knowledge when multiple Hermes Agents come online:

```python
#!/usr/bin/env python3
"""Multi-Agent bootstrap: preload shared knowledge into global scope."""

from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.config import SLMConfig

TEAM_KNOWLEDGE = [
    "Project Phoenix uses React 18 + TypeScript + Tailwind CSS",
    "API gateway deployed on Kong, backend in Go microservices",
    "Database: PostgreSQL 16, ORM: Prisma",
    "CI/CD: GitHub Actions, deploy to AWS EKS",
    "Repo: monorepo managed with Turborepo",
    "Coding standards: ESLint + Prettier, strict mode",
    "Testing strategy: Jest unit + Playwright E2E",
    "Release flow: feature branch → PR review → merge to main → auto deploy",
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

    print(f"Loaded {len(TEAM_KNOWLEDGE)} team knowledge items for profile={profile_id}")

# Initialize for each Agent
for agent in ["zhihui", "xiaoming", "xiaohong"]:
    bootstrap_team_knowledge(agent)
```

---

## Post-Import Operations

After importing, run these commands to optimize memories:

```bash
# Cognitive consolidation: dedup and merge similar memories
mslm consolidate --cognitive

# Check system health
mslm health

# View import results
mslm recall "imported knowledge" --limit 5
```

---

## Important Notes

1. **Deduplication**: MSLM's entropy gate auto-filters highly similar duplicate memories, but basic dedup before import is still recommended
2. **Rate control**: Pause 0.5s every 50 items during batch import to avoid pending queue buildup
3. **Scope selection**: When unsure, default to `personal` — you can adjust later via MCP tools
4. **Entity sharing**: The global entity mechanism means importing "React" knowledge makes the React entity auto-shared across all Agents — no manual sync needed
5. **Backward compatibility**: Memories without explicit scope default to `personal` and won't affect other Agents

---

## References

- [Multi-Scope Memory](multi-scope-memory-en.md) — Three-tier scope core concepts
- [Getting Started](getting-started-en.md) — From installation to daily use
- [SLM Technical Docs](slm/INDEX.md) — Upstream technical reference

---

*MSLM (Multi-Scope Local Memory) — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
