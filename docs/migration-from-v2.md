# Migration from V2
> SuperLocalMemory V4 Documentation
> https://superlocalmemory.com | Part of Qualixar

Upgrade from SuperLocalMemory V2 to V3. Verify backup before migrating; see
rollback caveats below. No `slm migrate --dry-run` exists.

> **V4.0.0 additive migrations:** V4.0.0 includes `M038_learning_feedback_channel`
> (eager, applied at startup on `learning.db`) and `M039_scene_fact_members`
> (deferred, applied once engine-owned tables exist on `memory.db`). Manual
> `slm db migrate` is not normally required for V4.0.0 itself. `slm db migrate` is
> **forward-only** (`status` / `--dry-run` / apply; no `slm db migrate
> --rollback` and no `slm migrate --rollback` for V4 DBs — see
> `src/superlocalmemory/cli/db_migrate.py` and
> `src/superlocalmemory/storage/migration_runner.py`). It refuses to run
> against a DB written by a newer build and holds back migrations whose
> dependency did not complete. Schema downgrade is unsupported — to revert a V4
> upgrade, restore a **verified pre-upgrade complete backup of the whole data
> root** ( `slm serve stop` first so WAL/SHM checkpoint, then copy all present
> `*.db` plus `-wal`/`-shm` sidecars and `lance/` if present). The `M039`
> projection is `scene_fact_members` with **profile-scoped composite**
> membership (see `src/superlocalmemory/storage/migrations/M039_scene_fact_members.py`).

---

## What Changed in V3

| Area | V2 | V3 |
|------|----|----|
| **Retrieval** | Single-channel semantic search | Five candidate producers (Semantic + BM25 + Temporal + Spreading-Activation + Hopfield) -> RRF fusion + entity-graph post-fusion enhancement |
| **Modes** | One mode (cloud required for smart features) | Three modes: A (zero-cloud), B (local LLM), C (cloud LLM) |
| **Math layer** | None | Fisher-Rao similarity, Sheaf consistency, Langevin lifecycle |
| **Ingestion** | Basic text storage | 11-step pipeline: entities, facts, emotions, beliefs, graph, and more |
| **Data directory** | `~/.claude-memory/` | `~/.superlocalmemory/` (the migrator attempts a legacy-path symlink; verify it) |
| **Consistency** | Manual | Automatic contradiction detection |
| **Recall quality** | Good | Significantly better on complex queries (multi-hop, temporal) |

**Compatibility boundary:** verify the commands, integrations, profiles, and
runtime artifacts your deployment relies on after migration. The migrator is a
data/schema migration, not a proof that every optional configuration, learned
state, or integration remains operational.

## Before You Migrate

1. **Update to the latest version:**

```bash
npm update -g superlocalmemory
```

2. **Check your current version:**

```bash
slm --version
# Should show 3.x.x or 4.x.x
```

3. **Preserve both data roots before migration** (do not rely on a live
   `memory.db` copy):

```bash
slm serve stop
# Copy the complete legacy V2 source ~/.claude-memory/ to an encrypted/private
# destination. If ~/.superlocalmemory/ already exists, preserve it separately.
# Include every present .db plus -wal/-shm sidecar and lance/ directory.
# Verify owner-only modes (0600/0700); destination follows process umask.
ls -la ~/.claude-memory/
# Keep the daemon stopped through `slm migrate`; restart only after migration
# and verification have completed.
```

> No `slm migrate --dry-run` exists for the V2→V3 migrator. For V4 additive
> DB migrations the inspect command is `slm db migrate --dry-run` (and
> `slm db migrate --status`), forward-only.

## Run the Migration

```bash
slm migrate
# After migration completes and its checks pass:
slm serve start
```

The migrator performs these code-defined steps:

1. Creates a backup of your V2 database (verify it is complete and
   owner-only before proceeding)
2. Copies data from `~/.claude-memory/` to `~/.superlocalmemory/`
3. Attempts to create a legacy-path symlink (`~/.claude-memory/ -> ~/.superlocalmemory/`); verify it before relying on old IDE configs
4. Extends the database schema and inserts migrated facts

It does not configure an operating mode, run a SQLite integrity check, or
guarantee that every optional embedding/BM25 projection has been rebuilt.
Run the post-migration checks below and re-embed/rebuild any optional indexes
required by your deployment.

> The migration spans file copies, SQLite commits, and a rename/symlink — not a
> single global transaction. Do **not** treat it as globally
> transactional/zero-loss without a verified pre-upgrade backup.

**V4 migrations after that:** `M038` (adds `learning_feedback.channel` for
`pattern_miner`) and the deferred `M039` normalized scene/fact projection are
applied automatically; see header note for DDL details.

## Migration boundaries

The migrator copies supported V2 memory data and attempts the legacy-path
symlink. Preserve and verify your pre-upgrade whole-root backup because it does
not prove complete continuity for optional indexes, configuration, audit/trust
history, or every runtime artifact in a customized installation.

## What Gets Added

The migration adds V3 capabilities to your existing data:

- BM25 token index for keyword search
- Entity graph nodes and edges
- Temporal event entries
- Fisher-Rao similarity metadata
- Sheaf consistency sections
- Langevin lifecycle state

These are available to configure after migration; do not assume every optional
projection has been materialized until its health/rebuild check succeeds.

## After Migration

### Verify

```bash
slm status --json
# or slm status for the text summary
slm db migrate --status   # shows M038/M039 applied state: see docs/cli-reference.md
```

Confirm:
- Configure and verify the intended operating mode; migration does not select one
- Memory count matches your V2 count (`slm status --json | jq '.data.fact_count'`)
- `slm db migrate --status` shows expected migrations as applied/verified

### Try a recall

```bash
slm recall "something you stored in V2"
```

Results should match or exceed V2 quality. V3's multi-producer retrieval finds memories that V2's single-channel search might have missed.

### Explore V3 features

```bash
slm trace "your query"       # See channel-by-channel breakdown
slm health                   # Check math layer status
slm mode b                   # Try local LLM mode (if Ollama installed)
# Use slm db migrate --status / --dry-run to inspect additive DB migrations
# (forward-only; no rollback). See `slm ops status` / `slm ops list` for
# stuck operations after upgrades.
```

## Rollback

Rollback of the V2→V3 `slm migrate` is **only** possible while a valid
pre-migration backup still exists and is **not** automatic or retained for
30 days. There is no automatic 30-day retention or timed deletion — verify the
backup file before migrating. Re-creating the backup during migration does not
guarantee a coherent cross-store set on the legacy per-file path
(`docs/cloud-backup.md`).

Downgrade of a V4 DB (M038/M039) is **unsupported**: there is no
`slm db migrate --rollback` (and no `slm migrate --rollback` for V4 DBs).
To revert a V4 upgrade, restore a verified **pre-upgrade complete backup of
the whole data root** (stop the daemon first — `slm serve stop` — and include
WAL/SHM sidecars plus `lance/` if present). Copying a live `memory.db` alone
while the daemon runs is unsafe and does not guarantee a coherent restore set.

## IDE Configuration Updates

### Automatic (best effort)

The migrator attempts to create a legacy-path symlink for compatible IDE
configurations. Check that it exists and test each IDE integration after
migration; a symlink failure is reported as a warning rather than a global
migration failure.

### Manual (optional)

If you want to update your IDE configs to use the new path directly:

```bash
slm connect
```

This updates all detected IDE configs to point to `~/.superlocalmemory/` instead of relying on the symlink.

## FAQ

**Q: Will my IDE break during migration?**
It may require repair. Confirm the legacy-path symlink and run a real
connection/recall check in each IDE you use; use `slm connect` to update
detected configurations directly.

**Q: Do I need to reconfigure my API keys?**
Possibly. The V2 migrator copies the database; it does not prove migration of
separate configuration or credential files. Reconfigure or supply keys through
environment variables as needed, then test the provider path you use.

**Q: Can I run V2 and V3 side by side?**
No. The migration converts your database in place (with backup). No side-by-side.

**Q: What if migration fails halfway?**
The migration spans multiple file copies/SQLite commits and a symlink; it is **not**
a globally atomic/transactional switch. Keep the verified pre-upgrade complete
backup (offline whole-root copy with daemon stopped) and restore that if needed.
Do not rely on an unverified live `memory.db` copy.

**Q: I have multiple profiles. Are they all migrated?**
Verify them explicitly. Confirm each expected profile appears and that its
recall boundaries still behave correctly before retiring the recovery copy.

**Q: How big will my database get after migration?**
There is no supported fixed percentage. Size depends on the source data,
enabled indexes, and later model/vector artifacts. Measure the backup and the
completed target root before deleting any recovery copy.

---

*SuperLocalMemory V4 — Copyright 2026 Varun Pratap Bhardwaj. AGPL-3.0-or-later. Part of Qualixar.*
