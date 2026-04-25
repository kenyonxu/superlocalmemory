# Multi-Scope Memory Design: Hermes Agent Team

> Phase 1 Spec — Two-Layer Scope (Personal + Global) with Point-to-Point Sharing
> Date: 2026-04-25

## Context

### Use Case

The target scenario is a single human user managing a team of AI agents (the "Hermes maid corps"). Each agent has its own `profile_id` and specialized domain. Currently there is one agent (知惠/zhihui, the head maid); more will follow.

Key characteristics:
- **Single human user** — no multi-user isolation requirements
- **Multiple AI agent profiles** — each agent is an independent personality with its own memory
- **Flat team structure** — no fixed subgroups; agents are distinguished by skill tags, not organizational membership
- **Personality-driven recall** — agents should recall personal experiences first, shared knowledge second

### Motivation

Agents need both private memories (shaping personality) and shared knowledge (team coordination). Without scope separation, every agent sees everything — no personality differentiation, no controlled information flow.

### Scope of This Spec

Phase 1: two-layer scope (personal + global) with point-to-point sharing as a supplement.

Future phases (out of scope but documented for context):
- Phase 2: skill-domain tags for automatic virtual grouping
- Phase 3: global authoritative entities + local references + semantic auto-upgrade

---

## Design Decisions

| Decision | Resolution |
|----------|-----------|
| Team structure | Flat, skill-tag based, no fixed groups |
| Scope layers | personal (per agent) + global (all agents) + shared_with (point-to-point) |
| Upgrade trigger | Phase 1: explicit instruction only. User says "tell everyone" → agent calls `remember(..., scope="global")` |
| RRF fusion weights | personal=1.0, global=0.5, shared_with=0.7 |
| Global writability | Any agent can write to global scope, but only when explicitly instructed by the user |
| Entity disambiguation | Phase 1: independent namespaces per scope. Phase 3: global authority + local references |
| Group management tools | Not needed in Phase 1 (flat team, no join/leave) |

---

## Architecture

### Data Model

```
┌─────────────────────────────────────────────────────┐
│  Scope Dimension                                     │
├──────────────────┬──────────────────────────────────┤
│  personal        │  global                          │
│  (per agent)     │  (all agents)                    │
├──────────────────┼──────────────────────────────────┤
│ profile_id       │  no profile restriction          │
│ + shared_with    │  readable/writable by any agent  │
│  (JSON array of  │  (requires explicit instruction) │
│   profile_ids)   │                                  │
└──────────────────┴──────────────────────────────────┘
```

### Store Flow

```
User conversation → Hermes Agent → remember(content, scope="personal")
                                      │
                       ┌──────────────┼──────────────┐
                       │              │              │
                  scope="personal"  scope="global"  shared_with=["xiaoming"]
                       │              │              │
                       ▼              ▼              ▼
                 Agent's personal  Global memory   Agent's personal
                 (only owner sees) (all agents)    (owner + listed agents)
```

### Recall Flow

```
Agent recall("query")
    ├── 7 channels × personal scope (profile_id = agent's id)    → weight 1.0
    ├── 7 channels × global scope (no profile filter)            → weight 0.5
    └── shared_with contains agent's profile_id                  → weight 0.7
                │
                ▼
        RRF fusion (k=60) → Top-K results
```

---

## Schema Changes

### Core Tables — Two New Columns Each

The following 8 tables gain `scope` and `shared_with` columns:

- `memories`
- `atomic_facts`
- `canonical_entities`
- `kg_nodes`
- `graph_edges`
- `memory_edges`
- `temporal_events`
- `audit_trail`

```sql
-- Example for atomic_facts (same pattern for all 8 tables)
ALTER TABLE atomic_facts ADD COLUMN scope TEXT NOT NULL DEFAULT 'personal';
ALTER TABLE atomic_facts ADD COLUMN shared_with TEXT;  -- JSON array: '["xiaoming","laozhang"]'

-- Indexes
CREATE INDEX idx_facts_scope ON atomic_facts(scope);
CREATE INDEX idx_facts_profile_scope ON atomic_facts(profile_id, scope);
```

### shared_with Design Rationale

`shared_with` is a JSON array stored in a TEXT column, not a separate relation table.

Reasons:
- Point-to-point sharing is low-frequency in Phase 1 (most memories are either personal or global)
- Avoids 8 new junction tables (one per core table)
- SQLite's `json_each()` supports efficient querying: `WHERE profile_id IN (SELECT value FROM json_each(shared_with))`

### Migration Strategy

- All existing data gets `scope='personal'` and `shared_with=NULL`
- Fully backward-compatible — no data loss, no behavior change for existing data
- Migration module: `storage/migrations/M0xx_add_scope_support.py`

---

## DatabaseManager Changes (~100 lines)

### Query Signature Extension

All 30+ query methods gain scope parameters:

```python
# Before
def search_facts_fts(self, query: str, profile_id: str, limit: int = 20)

# After
def search_facts_fts(
    self, query: str, profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    limit: int = 20,
)
```

### Unified WHERE Clause Pattern

Replace all instances of `WHERE profile_id = ?` with:

```sql
WHERE (
    (scope = 'personal' AND profile_id = ?)
    OR (scope = 'global')
    OR (? IN (SELECT value FROM json_each(shared_where)))
)
```

This pattern applies uniformly across all 30+ query methods.

---

## MemoryEngine Changes (~40 lines)

### Store Signature

```python
def store(
    self, content: str,
    profile_id: str | None = None,
    scope: str = "personal",
    shared_with: list[str] | None = None,
) -> StoreResult
```

### Recall Signature

```python
def recall(
    self, query: str,
    profile_id: str | None = None,
    include_global: bool = True,
    include_shared: bool = True,
    ...
) -> RecallResult
```

---

## StorePipeline Changes (~30 lines)

Transparent pass-through of `scope` and `shared_with` to the database layer. No logic changes — all existing fact extraction, entity resolution, and graph wiring remain unchanged.

---

## RecallPipeline Changes (~80 lines)

This is the largest single-file change.

### Current Flow

```
query → 7 channels (single profile) → RRF fusion → return
```

### Target Flow

```
query ──┬──► personal scope retrieval (profile_id = agent's id)
        ├──► global scope retrieval (no profile filter)
        └──► shared_with retrieval (items where agent's id is in shared_with)
              │
              ▼
        RRF fusion with scope weights (personal=1.0, global=0.5, shared=0.7)
              │
              ▼
        Top-K results
```

### Implementation

```python
def recall_pipeline(
    query: str,
    profile_id: str,
    include_global: bool = True,
    include_shared: bool = True,
    ...
) -> RecallResponse:
    all_results: dict[str, list[SearchResult]] = {}

    # 1. Personal scope (always runs)
    all_results["personal"] = _run_channels(query, profile_id, scope="personal")

    # 2. Global scope
    if include_global:
        all_results["global"] = _run_channels(query, profile_id=None, scope="global")

    # 3. Shared-with-me items
    if include_shared:
        all_results["shared"] = _run_shared_channels(query, profile_id)

    # 4. Weighted RRF fusion
    return _weighted_rrf_fusion(all_results, scope_weights={
        "personal": 1.0,
        "global": 0.5,
        "shared": 0.7,
    })
```

---

## Retrieval Channel Changes (~100 lines across 7 files)

Each of the 7 retrieval channels adds scope filtering:

| Channel | File | Change |
|---------|------|--------|
| Semantic | `semantic_channel.py` | Vector query WHERE adds scope condition |
| BM25 | `bm25_channel.py` | FTS query adds scope filter |
| Entity | `entity_channel.py` | Graph traversal adds scope boundary |
| Temporal | `temporal_channel.py` | Time query adds scope condition |
| Hopfield | `hopfield_channel.py` | Associative retrieval adds scope boundary |
| Profile | `profile_channel.py` | Profile filter expands to include global + shared |
| Spreading Activation | `spreading_activation.py` | Graph propagation adds scope limit |

Each channel follows the same pattern: replace `WHERE profile_id = ?` with the three-way OR condition.

---

## MCP Tool Changes (~60 lines)

### Existing Tool Extensions

```python
# remember
async def remember(
    content: str, tags: str = "",
    profile_id: str = "",
    scope: str = "personal",       # NEW: "personal" | "global"
    shared_with: str = "",         # NEW: comma-separated profile_ids
) -> dict

# recall
async def recall(
    query: str, limit: int = 10,
    profile_id: str = "",
    include_global: bool = True,   # NEW
    include_shared: bool = True,   # NEW
) -> dict
```

### No New Tools in Phase 1

Group management tools (join_group, leave_group, etc.) are not needed because the team is flat and there are no fixed subgroups.

---

## Migration Script

```python
# storage/migrations/M0xx_add_scope_support.py

TABLES = [
    'memories', 'atomic_facts', 'canonical_entities', 'kg_nodes',
    'graph_edges', 'memory_edges', 'temporal_events', 'audit_trail',
]

def upgrade(db):
    for table in TABLES:
        db.execute(f"ALTER TABLE {table} ADD COLUMN scope TEXT NOT NULL DEFAULT 'personal'")
        db.execute(f"ALTER TABLE {table} ADD COLUMN shared_with TEXT")
    for table in TABLES:
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_scope ON {table}(scope)")
        db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_profile_scope "
            f"ON {table}(profile_id, scope)"
        )

def downgrade(db):
    for table in TABLES:
        db.execute(f"DROP INDEX IF EXISTS idx_{table}_scope")
        db.execute(f"DROP INDEX IF EXISTS idx_{table}_profile_scope")
```

---

## Testing Strategy

### Test Pattern: Simulated Multi-Agent

Tests create multiple `MemoryEngine` instances with different `profile_id`s to simulate different agents:

```python
def test_cross_agent_global_visibility(in_memory_db, mock_embedder):
    engine_zhihui = MemoryEngine(config=config, profile_id="zhihui")
    engine_xiaoming = MemoryEngine(config=config, profile_id="xiaoming")

    # Zhihui stores global memory
    engine_zhihui.store("Project uses React 19", scope="global")

    # Xiaoming should be able to recall it
    results = engine_xiaoming.recall("React config", include_global=True)
    assert len(results) > 0
    assert "React 19" in results[0].content

    # But Zhihui's personal memory is invisible to Xiaoming
    engine_zhihui.store("I prefer Vim", scope="personal")
    results = engine_xiaoming.recall("Vim")
    assert all("I prefer Vim" not in r.content for r in results)
```

### Test Categories

1. **Unit tests**: store/recall behavior for each scope combination
2. **Integration tests**: cross-agent visibility (agent A's global memory visible to agent B)
3. **Edge case tests**: shared_with=null, global + personal same-name memories in RRF ranking
4. **Authorization tests**: personal memories cannot be recalled by other agents

---

## Effort Estimate

| Module | Lines Changed |
|--------|--------------|
| Schema + migration | ~50 |
| Models | ~20 |
| DatabaseManager | ~100 |
| MemoryEngine | ~40 |
| StorePipeline | ~30 |
| RecallPipeline | ~80 |
| Retrieval Channels (7 files) | ~100 |
| MCP Tools | ~60 |
| Config + migration | ~40 |
| **Total** | **~520** |

Compared to original roadmap (~1200 lines), this is a 57% reduction by eliminating group management, group permissions, join/leave tools, and entity alias tables.

---

## Future Phases (Out of Scope)

### Phase 2: Skill-Domain Tags (~300 lines)

- Add `domain_tags` column to core tables
- Agents declare skill tags in their profile
- Memories auto-tagged by `entity_resolver` during store
- Recall filters by matching agent skills to memory domains

### Phase 3: Global Authoritative Entities (~400 lines)

- Global `canonical_entities` are the authority
- Personal scope creates `entity_aliases` that reference global entities
- Cross-scope entity disambiguation during store
- Semantic classifier for automatic scope upgrade decisions
