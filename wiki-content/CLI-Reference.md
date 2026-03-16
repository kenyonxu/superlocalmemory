# CLI Reference

All `slm` commands grouped by function.

## Setup

| Command | Description |
|---------|-------------|
| `slm setup` | Run the setup wizard (IDE detection, MCP config) |
| `slm status` | Show system status (database, profile, mode, memory count) |
| `slm connect` | Auto-detect and connect to installed IDEs |
| `slm mode` | Show current operating mode |
| `slm mode a\|b\|c` | Switch operating mode |
| `slm health` | Show system health (consistency scores, lifecycle state) |

## Memory Operations

### Store

```bash
slm remember "Fixed the auth bug — JWT expiry was set to 1 hour instead of 24"
```

Store a memory. The system automatically extracts entities, facts, emotions, temporal markers, and builds graph connections.

Options:
- `--tags "tag1,tag2"` — Add tags
- `--importance high` — Set importance (low, medium, high)
- `--profile work` — Store to a specific profile

### Recall

```bash
slm recall "JWT token configuration"
```

Retrieve memories relevant to a query. Uses 4-channel retrieval (semantic, keyword, entity graph, temporal).

Options:
- `--limit 10` — Number of results (default: 5)
- `--mode a|b|c` — Override mode for this query
- `--trace` — Show channel breakdown (which retrieval channels contributed)

### Search

```bash
slm search "authentication"
```

Broader search across all memories. Returns partial matches and related content.

Options:
- `--limit 20` — Number of results
- `--type semantic|keyword|graph` — Restrict to one search type

### Forget

```bash
slm forget "JWT token configuration"
```

Delete memories matching the query. Supports targeted deletion.

Options:
- `--id <memory-id>` — Delete a specific memory by ID
- `--all` — Delete all memories (requires confirmation)
- `--before "2026-01-01"` — Delete memories before a date

### List

```bash
slm list
```

Show recent memories.

Options:
- `--limit 20` — Number to show
- `--sort date|importance|trust` — Sort order

## Profiles

```bash
slm profile                     # Show active profile
slm profile list                # List all profiles
slm profile create <name>       # Create a new profile
slm profile switch <name>       # Switch active profile
slm profile delete <name>       # Delete a profile (with confirmation)
```

Profiles provide complete memory isolation. Work, personal, and client memories never mix.

## Diagnostics

```bash
slm status                      # System overview
slm health                      # Consistency and lifecycle health
slm consistency                 # Run contradiction detection
slm benchmark locomo --mode c   # Run LoCoMo benchmark
slm stats                       # Memory statistics (count, size, age distribution)
```

## Migration

```bash
slm migrate                     # Upgrade V2 database to V3
slm migrate --rollback          # Undo migration (within 30 days)
slm migrate --status            # Check migration status
```

## Compliance

```bash
slm retention                   # Show retention policy
slm retention set --days 365    # Set retention period
slm audit                       # Show audit trail
slm export                      # Export all memories (JSON)
slm erasure --user <id>         # GDPR right-to-erasure
```

## Dashboard

```bash
slm dashboard                   # Open the web dashboard in your browser
```

The dashboard provides a visual interface for browsing memories, viewing the knowledge graph, monitoring events, and managing profiles.

## Global Options

These options work with any command:

| Option | Description |
|--------|-------------|
| `--profile <name>` | Override active profile for this command |
| `--json` | Output in JSON format |
| `--verbose` | Show detailed output |
| `--help` | Show help for any command |

## Examples

```bash
# Store a decision with context
slm remember "Chose PostgreSQL over MongoDB for the user service. Reason: ACID transactions needed for billing." --tags "architecture,database" --importance high

# Recall with trace to see which channels found what
slm recall "database decision for user service" --trace

# Search across all memories for a topic
slm search "billing" --limit 10

# Export for backup
slm export > backup-2026-03-16.json

# Check what mode you're in
slm mode
```

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
