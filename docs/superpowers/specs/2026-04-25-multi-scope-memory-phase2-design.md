# Multi-Scope Memory Phase 2 Design: Skill-Domain Tags

> Phase 2 Spec — Automatic Domain-Based Agent Grouping
> Date: 2026-04-25
> Builds on: Phase 1 (personal + global + shared_with)

## Context

### Use Case

Phase 1 implemented two-layer scope (personal + global) with point-to-point sharing (`shared_with`). When the agent team grows beyond 2-3 agents, manually specifying `shared_with` for every store becomes tedious.

Phase 2 adds **skill-domain tags**: agents declare their skill domains (e.g., `backend`, `frontend`), and memories are automatically tagged during store. At recall time, agents can see memories whose domain tags overlap with their declared skills — no manual pairing needed.

### Motivation

- Reduce manual `shared_with` configuration overhead
- Enable emergent knowledge sharing within skill domains
- Preserve Phase 1's isolation guarantees (domain matching only expands the shared scope)

### Scope

Phase 2 is limited to:
- Rule-based domain tag assignment (entity name → domain mapping table)
- Automatic domain tag propagation during store
- Recall-time domain matching via the existing shared scope channel
- ~50 built-in seed mappings for common tech entities

Out of scope:
- LLM-based automatic classification (future Phase 2B)
- Domain tag statistics or learning
- `remove_domain_mapping` / `list_domain_mappings` management tools
- Manual domain tag override at store time

---

## Design Decisions

| Decision | Resolution |
|----------|-----------|
| Tag source | Rule-based: entity name → domain mapping table |
| Agent skill declaration | `skill_tags` in profile `config` dict (`config_json` column) |
| Domain matching mechanism | Reuse Phase 1 shared scope channel (weight=0.7) |
| Schema change | New `domain_mapping` table + `domain_tags` column on 4 core tables |
| Seed data | ~50 built-in tech entity mappings, extensible via MCP tool |
| LLM classification | Not in Phase 2 (deferred to Phase 2B) |

---

## Architecture

### Data Model

```
┌─────────────────────────────────────────────────────────┐
│  domain_mapping (new table)                             │
├──────────────────┬──────────────────────────────────────┤
│  entity_name     │  domain                              │
│  "React"         │  "frontend"                          │
│  "PostgreSQL"    │  "backend"                           │
│  "Docker"        │  "devops"                            │
│  PRIMARY KEY (entity_name, domain)                      │
└──────────────────┴──────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  atomic_facts (+ 4 other core tables)                   │
├──────────────────┬──────────────────────────────────────┤
│  domain_tags     │  TEXT  (JSON: '["frontend","backend"]') │
│  NULL = no domain matched                               │
└──────────────────┴──────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  profiles.config_json (stored as JSON, parsed to dict)  │
├─────────────────────────────────────────────────────────┤
│  {"skill_tags": ["backend", "devops"]}                   │
│  Profile.config: dict = {"skill_tags": [...]}            │
└─────────────────────────────────────────────────────────┘
```

### Store Flow

```
content → entity_resolver → canonical entities: {"React": "react_01", "TypeScript": "ts_01"}
                                    ↓
                             resolve_domain_tags(["React", "TypeScript"])
                                    ↓
                             domain_mapping lookup → ["frontend"]
                                    ↓
                             domain_tags = ["frontend"]
                                    ↓
                             Write to atomic_facts.domain_tags
```

### Recall Flow (Extended Shared Scope)

```
Phase 1 shared scope WHERE:
  ? IN json_each(shared_with)

Phase 2 extended shared scope WHERE:
  (? IN json_each(shared_with))
  OR (domain_tags IS NOT NULL AND EXISTS (
      SELECT 1 FROM json_each(domain_tags)
      WHERE value IN (agent_skill_tags)
  ))
```

---

## Schema Changes

### New Table: domain_mapping

```sql
CREATE TABLE IF NOT EXISTS domain_mapping (
    entity_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    PRIMARY KEY (entity_name, domain)
);
```

### New Column: domain_tags

The following 4 tables gain a `domain_tags` column (tables involved in recall WHERE clauses):

- `atomic_facts`
- `canonical_entities`
- `graph_edges`
- `temporal_events`

Note: `memories` is excluded — the recall pipeline never applies `_scope_where` to it.

```sql
ALTER TABLE atomic_facts ADD COLUMN domain_tags TEXT;  -- JSON array or NULL
-- Same for the other 3 tables
```

### Migration

- `storage/migrations/M015_add_domain_tags.py`
- Creates `domain_mapping` table
- Adds `domain_tags` column to 4 core tables
- Seeds `domain_mapping` via `post_ddl_hook` (see M002 for precedent)
- All existing data gets `domain_tags = NULL` (no behavior change)

Migration registration (in `storage/migration_runner.py`):
1. Import M015 at top
2. Add to `_MODULES` dict: `"M015": M015_add_domain_tags`
3. Add `Migration("M015", ...)` to `DEFERRED_MIGRATIONS` list

Migration must include a `verify(conn)` function for crash-recovery idempotency (matches M014/M011 convention).

---

## DatabaseManager Changes (~30 lines)

### New Method: resolve_domain_tags

```python
def resolve_domain_tags(self, entity_names: list[str]) -> list[str]:
    """Batch lookup entity names → deduplicated domain tags."""
    if not entity_names:
        return []
    placeholders = ",".join("?" * len(entity_names))
    rows = self.execute(
        f"SELECT DISTINCT domain FROM domain_mapping "
        f"WHERE entity_name IN ({placeholders})",
        tuple(entity_names),
    )
    return [r["domain"] for r in rows]
```

### _scope_where Extension

The `include_shared` condition in `_scope_where()` gains domain tag matching:

```python
if include_shared:
    conditions.append("? IN (SELECT value FROM json_each(shared_with))")
    params.append(profile_id)

# Phase 2: domain tag overlap matching
# Guard: domain_tags IS NOT NULL prevents json_each(NULL) error
if include_shared and skill_tags:
    domain_placeholders = ",".join("?" * len(skill_tags))
    conditions.append(
        f"({prefix}domain_tags IS NOT NULL AND EXISTS ("
        f"SELECT 1 FROM json_each({prefix}domain_tags) "
        f"WHERE value IN ({domain_placeholders})))"
    )
    params.extend(skill_tags)
```

The `_scope_where` signature gains an optional `skill_tags` parameter:
```python
@staticmethod
def _scope_where(
    profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    table_alias: str = "",
    skill_tags: list[str] | None = None,  # NEW
) -> tuple[str, list]:
```

All callers that pass `include_shared=True` should also pass `skill_tags` when available.

---

## StorePipeline Changes (~20 lines)

Domain tag lookup happens **after entity resolution** in `enrich_fact()`, where canonical entity names are available:

```python
# In enrich_fact() (~line 74), after entity_resolver.resolve() returns:
canonical = entity_resolver.resolve(fact.entities, profile_id)
fact.canonical_entities = list(canonical.values())

# Phase 2: resolve domain tags from canonical entity names
if canonical:
    fact.domain_tags = db.resolve_domain_tags(list(canonical.keys()))
```

`run_store()` then propagates `domain_tags` from enriched facts to `AtomicFact` DB INSERT statements.

Note: `enrich_fact()` currently doesn't receive `db` as a parameter. It must be added as a keyword argument, passed from `run_store()` which already has it.

---

## RecallPipeline + RetrievalEngine Changes (~30 lines)

### Skill Tags Propagation

`skill_tags` flows from the active profile through the recall chain:

```
SLMConfig.skill_tags → engine.recall()
  → recall_pipeline.run_recall()
    → retrieval_engine.recall()
      → stored as self._skill_tags at engine init
```

### RetrievalEngine Constructor

Add `skill_tags` as a constructor parameter (not from RetrievalConfig, which is a separate dataclass):

```python
def __init__(
    self,
    config: RetrievalConfig,
    db: DatabaseManager,
    # ... existing params ...
    skill_tags: list[str] | None = None,  # NEW
):
    self._skill_tags = skill_tags or []
```

### How skill_tags reach _scope_where

Channels don't accept `skill_tags` directly. Instead, `_run_channels()` passes `skill_tags` to DB method calls **inside** each channel when `scope="shared"`. The cleanest approach:

1. `_run_channels(scope="shared")` stores `skill_tags` in a temporary attribute before calling channels
2. Channels that call DB methods with `include_shared=True` read `self._db._current_skill_tags` (thread-local)
3. DB methods pass it to `_scope_where()`

**Simpler alternative** (recommended for Phase 2): Add `skill_tags` as a keyword argument to each channel's `search()` method (same pattern as `scope` in Phase 1). Channels pass it to DB calls that use `include_shared=True`.

```python
# In each channel's search():
def search(self, query, profile_id, top_k=50, *, scope="personal", skill_tags=None):
    # When calling DB methods with include_shared=True:
    facts = self._db.get_all_facts(profile_id, include_shared=True, skill_tags=skill_tags)
```

### BM25 Channel Limitation

The BM25 channel currently ignores scope filtering entirely (it loads all facts for a profile). Domain tag matching will NOT affect BM25 results in Phase 2. This is acceptable — BM25 results still get RRF-fused with other channels that do support domain filtering. A future PR can add scope-aware BM25.

---

## Profile Changes (~15 lines)

### Profile Dataclass

The existing `Profile` dataclass (`core/profiles.py`) has a `config: dict[str, Any]` field (not a JSON string). Add a convenience property:

```python
@dataclass(frozen=True)
class Profile:
    profile_id: str
    name: str
    # ... existing fields ...
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def skill_tags(self) -> list[str]:
        return self.config.get("skill_tags", [])
```

### SLMConfig

Add `skill_tags` field:
```python
@dataclass
class SLMConfig:
    # ... existing fields ...
    skill_tags: list[str] = field(default_factory=list)
```

Loaded from the active profile's `config.get("skill_tags", [])`.

---

## MCP Tool: add_domain_mapping (~20 lines)

```python
@server.tool()
async def add_domain_mapping(entity_name: str, domain: str) -> dict:
    """Add an entity-to-domain mapping for skill-based memory sharing.

    Example: add_domain_mapping("Kubernetes", "devops")
    """
    try:
        engine = get_engine()
        engine._db.execute(
            "INSERT OR IGNORE INTO domain_mapping (entity_name, domain) VALUES (?, ?)",
            (entity_name, domain),
        )
        return {"success": True, "mapping": {"entity_name": entity_name, "domain": domain}}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
```

---

## Seed Data (~50 entries)

Stored in `storage/seed_domain_mapping.py` as a list of tuples:

```python
SEED_DOMAIN_MAPPINGS = [
    # frontend
    ("React", "frontend"), ("Vue", "frontend"), ("Angular", "frontend"),
    ("Svelte", "frontend"), ("CSS", "frontend"), ("HTML", "frontend"),
    ("Tailwind", "frontend"), ("webpack", "frontend"), ("Vite", "frontend"),
    ("Next.js", "frontend"),
    # backend
    ("PostgreSQL", "backend"), ("MySQL", "backend"), ("Redis", "backend"),
    ("Django", "backend"), ("FastAPI", "backend"), ("Express", "backend"),
    ("SQLAlchemy", "backend"), ("MongoDB", "backend"), ("GraphQL", "backend"),
    ("REST", "backend"),
    # devops
    ("Docker", "devops"), ("Kubernetes", "devops"), ("Terraform", "devops"),
    ("Jenkins", "devops"), ("GitHub Actions", "devops"), ("Nginx", "devops"),
    ("CI/CD", "devops"), ("AWS", "devops"), ("GCP", "devops"),
    # mobile
    ("Flutter", "mobile"), ("React Native", "mobile"), ("Swift", "mobile"),
    ("Kotlin", "mobile"),
    # data
    ("Pandas", "data"), ("NumPy", "data"), ("PyTorch", "data"),
    ("TensorFlow", "data"), ("Spark", "data"),
]
```

Inserted during M015 migration.

---

## Testing Strategy

| Category | Test | Validates |
|----------|------|-----------|
| Schema | `domain_mapping` table exists | Migration ran |
| Schema | `domain_tags` column on 4 tables | Schema updated |
| Store | Entity matches → tag written | `resolve_domain_tags()` works |
| Store | No match → domain_tags is NULL | Graceful fallback |
| Store | Multiple entities same domain → deduplicated tag | `["frontend"]` not `["frontend","frontend"]` |
| Recall | Agent skill overlaps memory domain → visible | `_scope_where` domain condition |
| Recall | Agent skill doesn't overlap → invisible | Isolation preserved |
| Recall | Both shared_with and domain match → both visible | Two sharing mechanisms coexist |
| Profile | `skill_tags` parsed from `config` dict | Profile property works |
| Seed | Seed data inserted after migration | ~50 rows in domain_mapping |

---

## Effort Estimate

| Module | Lines |
|--------|-------|
| Schema + migration + seed + registration | ~90 |
| DatabaseManager (new method + _scope_where + callers) | ~40 |
| StorePipeline (domain tag lookup in enrich_fact) | ~25 |
| RecallPipeline + RetrievalEngine (skill_tags flow) | ~40 |
| Profile (skill_tags property + SLMConfig) | ~15 |
| MCP tool (add_domain_mapping) | ~20 |
| Tests | ~90 |
| **Total** | **~320** |

---

## Future Phases (Out of Scope)

### Phase 2B: LLM-Based Classification (~100 lines)
- Mode B/C: LLM classifies entities that miss the rule-based mapping
- New mappings auto-inserted into `domain_mapping` table
- Gradually builds up coverage without manual curation

### Phase 3: Global Authoritative Entities (~400 lines)
- Global `canonical_entities` are the authority
- Personal scope creates `entity_aliases` that reference global entities
- Cross-scope entity disambiguation during store
