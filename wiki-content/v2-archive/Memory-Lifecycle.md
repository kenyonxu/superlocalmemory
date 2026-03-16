# Memory Lifecycle Management

SuperLocalMemory automatically manages memory freshness so your system stays fast and relevant without manual cleanup.

## How It Works

Every memory moves through lifecycle states based on how often you use it:

| State | What It Means | Your Memories |
|-------|--------------|---------------|
| **Active** | Recently used, immediately available | Returned first in search results |
| **Warm** | Used recently but not today | Still searchable, slightly lower priority |
| **Cold** | Haven't been used in a while | Searchable but ranked lower |
| **Archived** | Old and unused | Compressed to save space, still recoverable |
| **Tombstoned** | Marked for deletion | Will be permanently removed |

## Automatic Transitions

You don't need to do anything — the system evaluates memories periodically and transitions them automatically. When you recall an archived memory, it's immediately promoted back to Active.

## MCP Tools

### Check Lifecycle Status
```
get_lifecycle_status
```
Shows the current state distribution of your memories.

### Compact Memories
```
compact_memories
```
Triggers lifecycle evaluation and compaction. Use `dry_run: true` to preview what would change.

## Bounded Growth

SuperLocalMemory enforces configurable storage limits. When your memory database approaches the limit, the oldest and least-used memories are archived or tombstoned first — ensuring your most valuable memories are always preserved.

## What This Means For You

- **No manual cleanup** — the system handles it
- **Fast search** — only active/warm memories are prioritized
- **Nothing lost** — archived memories can always be reactivated
- **Bounded storage** — your database never grows unbounded

---

**See also:** [[Behavioral-Learning]] · [[Enterprise-Compliance]] · [[Upgrading-to-v2.8]]
