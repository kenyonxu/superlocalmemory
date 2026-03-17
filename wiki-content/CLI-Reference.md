# CLI Reference

All `slm` commands — V3 has 15 commands grouped by function.

## Setup & Status

| Command | Description |
|---------|-------------|
| `slm setup` | Run the interactive setup wizard (mode selection, provider config) |
| `slm status` | Show system status (mode, database path, DB size) |
| `slm mode` | Show current operating mode |
| `slm mode a\|b\|c` | Switch operating mode |
| `slm provider` | Show current LLM provider |
| `slm provider set` | Configure LLM provider (Mode B/C) |
| `slm health` | Show math layer health (Fisher-Rao, Sheaf, Langevin stats) |
| `slm warmup` | Pre-download embedding model (~500MB, one-time) |

## Memory Operations

### Store

```bash
slm remember "Fixed the auth bug — JWT expiry was set to 1 hour instead of 24"
```

Store a memory. The system automatically extracts entities, facts, emotional signals, temporal markers, and builds graph connections.

Options:
- `--tags "tag1,tag2"` — Add tags

### Recall

```bash
slm recall "JWT token configuration"
```

Retrieve memories relevant to a query. Uses 4-channel retrieval (semantic, keyword, entity graph, temporal) with RRF fusion and cross-encoder reranking.

Options:
- `--limit 10` — Number of results (default: 20)

### Trace

```bash
slm trace "JWT token configuration"
```

Same as recall, but shows per-channel score breakdown — which retrieval channel (semantic, BM25, entity, temporal) contributed to each result. Useful for debugging retrieval quality.

### Forget

```bash
slm forget "JWT token configuration"
```

Delete memories matching a query. Shows matching memories and asks for confirmation before deleting.

## IDE Integration

```bash
slm connect        # Auto-detect and configure all installed IDEs
slm connect --list # Show which IDEs are configured
slm mcp            # Start MCP server (stdio transport — used by IDEs)
```

The `slm mcp` command is what your IDE calls internally. You typically don't run it directly — your IDE's MCP config handles it:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "slm",
      "args": ["mcp"]
    }
  }
}
```

## Profiles

```bash
slm profile list              # List all profiles
slm profile create <name>     # Create a new profile
slm profile switch <name>     # Switch active profile
```

Profiles provide complete memory isolation. Work, personal, and client memories never mix.

## Migration

```bash
slm migrate                   # Upgrade V2 database to V3
slm migrate --rollback        # Undo migration
```

## Dashboard

```bash
slm dashboard                 # Open web dashboard at http://localhost:8765
slm dashboard --port 9000     # Use a custom port
```

17-tab dashboard: memory browser, knowledge graph, recall lab, trust scores, math health, compliance, learning, IDE connections, settings, and more.

## Examples

```bash
# Store a decision with tags
slm remember "Chose PostgreSQL over MongoDB for the user service. Reason: ACID transactions needed for billing." --tags "architecture,database"

# Recall with channel breakdown
slm trace "database decision for user service"

# Check system status
slm status

# Check math layer health
slm health

# Switch to full power mode
slm mode c

# Open the dashboard
slm dashboard
```

## Complete Command List

| # | Command | What It Does |
|:-:|---------|-------------|
| 1 | `slm setup` | Interactive first-time wizard |
| 2 | `slm mode [a\|b\|c]` | Get or set operating mode |
| 3 | `slm provider [set]` | Get or set LLM provider |
| 4 | `slm connect [--list]` | Configure IDE integrations |
| 5 | `slm migrate [--rollback]` | V2 to V3 migration |
| 6 | `slm remember "..."` | Store a memory |
| 7 | `slm recall "..." [--limit N]` | Search memories |
| 8 | `slm forget "..."` | Delete matching memories |
| 9 | `slm status` | System status |
| 10 | `slm health` | Math layer health |
| 11 | `slm trace "..."` | Recall with channel breakdown |
| 12 | `slm mcp` | Start MCP server (for IDE) |
| 13 | `slm warmup` | Pre-download embedding model |
| 14 | `slm dashboard [--port N]` | Launch web dashboard |
| 15 | `slm profile list\|create\|switch` | Profile management |

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
