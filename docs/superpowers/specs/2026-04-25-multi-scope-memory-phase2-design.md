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
| Agent skill declaration | `skill_tags` field in profile `config_json` |
| Domain matching mechanism | Reuse Phase 1 shared scope channel (weight=0.7) |
| Schema change | New `domain_mapping` table + `domain_tags` column on 5 core tables |
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
│  profiles.config_json                                    │
├─────────────────────────────────────────────────────────┤
│  {"skill_tags": ["backend", "devops"]}                   │
└─────────────────────────────────────────────────────────┘
```

### Store Flow

```
content → entity_resolver → ["React", "TypeScript"]
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
  OR EXISTS (
      SELECT 1 FROM json_each(domain_tags)
      WHERE value IN (agent_skill_tags)
  )
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

The following 5 tables gain a `domain_tags` column:

- `memories`
- `atomic_facts`
- `canonical_entities`
- `graph_edges`
- `temporal_events`

```sql
ALTER TABLE atomic_facts ADD COLUMN domain_tags TEXT;  -- JSON array or NULL
-- Same for the other 4 tables
```

### Migration

- `storage/migrations/M015_add_domain_tags.py`
- Creates `domain_mapping` table
- Adds `domain_tags` column to 5 core tables
- Inserts seed data from `storage/seed_domain_mapping.py`
- All existing data gets `domain_tags = NULL` (no behavior change)

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
if include_shared and skill_tags:
    domain_placeholders = ",".join("?" * len(skill_tags))
    conditions.append(
        f"EXISTS (SELECT 1 FROM json_each({prefix}domain_tags) "
        f"WHERE value IN ({domain_placeholders}))"
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

After entity resolution in `run_store()`, add domain tag lookup:

```python
# After: entities = entity_resolver.resolve(content, ...)
# New:
domain_tags = db.resolve_domain_tags(list(entities.keys())) if entities else []
```

Then propagate `domain_tags` to `MemoryRecord`, `AtomicFact`, etc.

---

## RecallPipeline + RetrievalEngine Changes (~30 lines)

### Skill Tags Propagation

```python
# engine.recall() reads skill_tags from profile config
skill_tags = self._profile_config.get("skill_tags", [])

# Pass through recall_pipeline → retrieval_engine → _run_channels → DB methods
```

The `_run_channels(scope="shared")` pass needs `skill_tags` to reach `_scope_where`. This flows through:
- `retrieval_engine.recall()` → `_run_channels()` → channel `.search()` → DB methods

Each channel's DB calls that pass `include_shared=True` should also pass `skill_tags`.

### RetrievalEngine._run_channels

The `_run_channels` method passes `skill_tags` to channel search calls when `scope="shared"`:

```python
if scope == "shared":
    # Channels pass skill_tags to DB methods with include_shared=True
    channel_kwargs = {"skill_tags": self._skill_tags}
else:
    channel_kwargs = {}
```

### RetrievalEngine Constructor

Store `skill_tags` from config:
```python
self._skill_tags: list[str] = config.get("skill_tags", [])
```

---

## Profile Changes (~15 lines)

### Profile Dataclass

Add a convenience property that parses `skill_tags` from `config_json`:

```python
@dataclass(frozen=True)
class Profile:
    profile_id: str
    name: str
    # ... existing fields ...
    config_json: str = "{}"

    @property
    def skill_tags(self) -> list[str]:
        try:
            return json.loads(self.config_json).get("skill_tags", [])
        except (json.JSONDecodeError, AttributeError):
            return []
```

### SLMConfig

Add `skill_tags` field:
```python
@dataclass
class SLMConfig:
    # ... existing fields ...
    skill_tags: list[str] = field(default_factory=list)
```

Loaded from the active profile's `config_json.skill_tags`.

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
| Schema | `domain_tags` column on 5 tables | Schema updated |
| Store | Entity matches → tag written | `resolve_domain_tags()` works |
| Store | No match → domain_tags is NULL | Graceful fallback |
| Store | Multiple entities same domain → deduplicated tag | `["frontend"]` not `["frontend","frontend"]` |
| Recall | Agent skill overlaps memory domain → visible | `_scope_where` domain condition |
| Recall | Agent skill doesn't overlap → invisible | Isolation preserved |
| Recall | Both shared_with and domain match → both visible | Two sharing mechanisms coexist |
| Profile | `skill_tags` parsed from config_json | Profile property works |
| Seed | Seed data inserted after migration | ~50 rows in domain_mapping |

---

## Effort Estimate

| Module | Lines |
|--------|-------|
| Schema + migration + seed | ~80 |
| DatabaseManager (new method + _scope_where) | ~30 |
| StorePipeline (domain tag lookup) | ~20 |
| RecallPipeline + RetrievalEngine (skill_tags flow) | ~30 |
| Profile (skill_tags property + config) | ~15 |
| MCP tool (add_domain_mapping) | ~20 |
| Tests | ~80 |
| **Total** | **~275** |

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
