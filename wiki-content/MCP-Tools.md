# MCP Tools

SuperLocalMemory exposes 24 tools and 6 resources via the Model Context Protocol (MCP). These are what your IDE uses to interact with the memory system.

## Core Tools (13)

These tools handle the primary memory operations.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `remember` | `content`, `tags?`, `importance?` | Store a new memory |
| `recall` | `query`, `limit?` | Retrieve relevant memories |
| `search` | `query`, `type?`, `limit?` | Search across all memories |
| `forget` | `query` or `id` | Delete matching memories |
| `list_recent` | `limit?` | List recent memories |
| `get_status` | — | System status (db, mode, count) |
| `build_graph` | — | Rebuild the knowledge graph |
| `get_attribution` | `memory_id` | Get provenance chain for a memory |
| `compact_memories` | — | Compress and optimize storage |
| `memory_used` | — | Storage usage statistics |
| `fetch` | `id` | Get a specific memory by ID |
| `backup_status` | — | Backup and database health |
| `audit_trail` | `limit?` | Recent operations log |

## Management Tools (6)

These tools handle profiles, compliance, and configuration.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `switch_profile` | `name` | Switch to a different memory profile |
| `set_retention_policy` | `days`, `categories?` | Set data retention period |
| `report_outcome` | `memory_id`, `outcome` | Report whether a recalled memory was helpful |
| `correct_pattern` | `pattern_id`, `correction` | Correct a learned behavioral pattern |
| `get_behavioral_patterns` | `limit?` | View learned patterns |
| `get_learned_patterns` | `limit?` | View ML-learned recall patterns |

## V3 Tools (5)

New in V3 — mathematical foundations and advanced retrieval.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_lifecycle_status` | — | Memory lifecycle health (active/warm/cold counts) |
| `recall` (with `--trace`) | `query`, `trace: true` | Recall with channel breakdown showing which retrieval method found each result |
| `get_status` (V3) | — | Extended status including mode, math layer health, consistency score |

**Note:** V3 tools are being expanded during the integration phase. Additional tools for consistency checking, mode switching, and health monitoring are planned.

## Resources (6)

MCP resources provide read-only data streams that IDEs can subscribe to.

| Resource | URI | Description |
|----------|-----|-------------|
| Memory Stats | `memory://stats` | Total memories, storage size, profile count |
| Recent Memories | `memory://recent` | Last 10 memories stored |
| Active Profile | `memory://profile` | Current profile name and settings |
| System Health | `memory://health` | Database status, consistency score |
| Knowledge Graph | `memory://graph` | Graph summary (nodes, edges, communities) |
| Learning State | `memory://learning` | ML model state and learned patterns |

## How MCP Integration Works

1. Your IDE connects to the SuperLocalMemory MCP server
2. When you chat with your AI, the IDE calls `recall` with relevant context
3. SuperLocalMemory returns matching memories
4. The IDE injects those memories into the AI's context
5. Your AI responds with knowledge of your past work

This happens automatically — you do not need to manually call tools.

## Manual Tool Calls

You can also call tools explicitly in your IDE:

```
slm:remember "The deploy script needs AWS_REGION set to us-east-1"
slm:recall "deploy configuration"
slm:search "AWS settings"
```

The exact syntax depends on your IDE. Claude Code uses the `slm:` prefix. Other IDEs may differ.

## Configuration

The MCP server runs on a local Unix socket by default. Configuration is in your IDE's MCP settings file:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "superlocalmemory",
      "args": ["--mcp"]
    }
  }
}
```

See [IDE Setup](IDE-Setup) for per-IDE configuration paths.

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
