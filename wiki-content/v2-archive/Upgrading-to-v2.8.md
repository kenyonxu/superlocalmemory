# Upgrading to v2.8

SuperLocalMemory v2.8 is a drop-in upgrade. All existing memories, settings, and configurations are preserved.

## What's New

### Memory Lifecycle Management
Memories now automatically manage their own freshness. Active memories are prioritized, unused memories are archived, and storage growth is bounded.
→ [[Memory-Lifecycle]]

### Behavioral Learning
The system learns from action outcomes — which memories lead to success — and surfaces the best ones more often.
→ [[Behavioral-Learning]]

### Enterprise Compliance
Access control, immutable audit trails, and retention policy management for regulated environments.
→ [[Enterprise-Compliance]]

### 6 New MCP Tools
| Tool | Purpose |
|------|---------|
| `report_outcome` | Record action outcomes for behavioral learning |
| `get_lifecycle_status` | View memory lifecycle state distribution |
| `set_retention_policy` | Configure retention policies |
| `compact_memories` | Trigger lifecycle transitions |
| `get_behavioral_patterns` | View learned behavioral patterns |
| `audit_trail` | Query the compliance audit trail |

## How to Upgrade

```bash
npm install -g superlocalmemory@latest
```

That's it. The system automatically:
1. Migrates your database schema (backward compatible)
2. Enables lifecycle management for existing memories
3. Starts behavioral learning from your next session
4. Creates audit.db for compliance logging

## Backward Compatibility

- All existing MCP tools work exactly as before
- All existing memories are preserved with ACTIVE lifecycle state
- Search results include the same memories (lifecycle-aware ranking is additive)
- No configuration changes required

## New Databases

v2.8 introduces two additional database files (created automatically):
- `~/.claude-memory/learning.db` — Behavioral learning data (GDPR-erasable)
- `~/.claude-memory/audit.db` — Compliance audit trail (retention-governed)

Your existing `memory.db` is unchanged.

---

**See also:** [[Memory-Lifecycle]] · [[Behavioral-Learning]] · [[Enterprise-Compliance]]
