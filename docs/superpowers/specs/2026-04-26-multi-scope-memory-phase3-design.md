# Multi-Scope Memory Phase 3 Design: Global Authoritative Entities

> Phase 3 Spec — Unified Entity Identity via Global-First Resolution
> Date: 2026-04-26
> Builds on: Phase 1 (personal/global/shared_with) + Phase 2 (domain tags)

## Context

### Use Case

Phases 1 and 2 gave each agent independent entity namespaces. When agent A (zhihui) and agent B both store memories about "React", two separate `canonical_entities` rows are created — one per `profile_id` in `scope='personal'`. The knowledge graph edges, domain tags, and temporal events attached to each entity are also isolated.

For the Hermes agent team (flat structure, same human user), this isolation is unnecessary for most technology entities. "React" is the same concept regardless of which agent mentions it. Shared entities enable shared graph edges, shared domain tags, and more effective cross-agent knowledge.

### Motivation

- Eliminate duplicate entities for shared concepts (React, Python, Docker...)
- Share knowledge graph edges across all agents automatically
- Domain tags assigned once benefit all agents
- Simpler entity space — one "React" instead of N copies

### Scope

Phase 3 is limited to:
- Global-first entity resolution: `resolve()` checks global scope before personal
- New entities default to `scope='global'` (any agent can create)
- Reuse existing `canonical_entities.scope` column — zero new tables, zero migrations
- Backward compatible: existing personal entities remain unchanged

Out of scope:
- Semantic classifier for automatic scope upgrade (deferred)
- Personal-only entity concept (all new entities are global)
- Entity merge tool for consolidating existing personal duplicates
- LLM-based scope decisions

---

## Design Decisions

| Decision | Resolution |
|----------|-----------|
| Entity authority | Global `canonical_entities` are the authority; personal entities are legacy |
| Resolution order | Global first, then personal fallback, then create new (in global) |
| New entity scope | Default `scope='global'` for all newly created entities |
| Creation permission | Any agent can create global entities |
| Existing personal entities | Unchanged — no migration, no merge, no deletion |
| Schema changes | None — `canonical_entities` already has `scope` column from Phase 1 |
| New tables | None |

---

## Architecture

### Current Resolution Flow (Phase 1/2)

```
resolve("React", profile_id="zhihui")
  Tier a: get_entity_by_name("React", "zhihui")
    → WHERE canonical_name ILIKE 'React' AND profile_id = 'zhihui'
    → Found: entity_id="abc123" (personal scope)
  → Return "abc123"
```

Agent B stores "React":
```
resolve("React", profile_id="xiaoming")
  Tier a: get_entity_by_name("React", "xiaoming")
    → Not found
  Tier b/c: alias/fuzzy → not found
  → Create new: entity_id="def456" (personal scope, profile_id="xiaoming")
```

Result: two independent entities for the same concept.

### Phase 3 Resolution Flow

```
resolve("React", profile_id="zhihui")
  Tier 0 (NEW): get_entity_by_name_global("React")
    → WHERE canonical_name ILIKE 'React' AND scope = 'global'
    → Found: entity_id="global_react_01"
  → Return "global_react_01"

  (Tier a/b/c/d only reached if global lookup fails)
```

Agent B stores "React":
```
resolve("React", profile_id="xiaoming")
  Tier 0: get_entity_by_name_global("React")
    → Found: entity_id="global_react_01" (same entity!)
  → Return "global_react_01"

  No duplicate created.
```

### Entity Creation Flow

```
resolve("ObscureLib", profile_id="zhihui")
  Tier 0: get_entity_by_name_global("ObscureLib") → Not found
  Tier a: get_entity_by_name("ObscureLib", "zhihui") → Not found
  Tier b/c/d: → Not found
  → Create new entity with scope='global' (not 'personal')
```

---

## Code Changes

### 1. DatabaseManager: `get_entity_by_name()` scope parameter (~5 lines)

File: `src/superlocalmemory/storage/database.py`

Add `scope` parameter to the existing `get_entity_by_name()` method:

```python
def get_entity_by_name(
    self, name: str, profile_id: str, *, scope: str | None = None,
) -> CanonicalEntity | None:
```

When `scope` is provided, the query adds `AND scope = ?` condition. This allows callers to explicitly query for global-scope entities.

### 2. EntityResolver: Global-first resolution tier (~20 lines)

File: `src/superlocalmemory/encoding/entity_resolver.py`

Add a new Tier 0 before the existing Tier a in `resolve()`:

```python
# Tier 0 (Phase 3): check global scope first
global_entity = self._db.get_entity_by_name(name, profile_id, scope="global")
if global_entity is not None:
    resolution[raw] = global_entity.entity_id
    self._touch_last_seen(global_entity.entity_id)
    continue
```

This goes after the stop-word/length filters and before the existing "Tier a: exact match" comment.

### 3. EntityResolver: New entities default to global scope (~3 lines)

File: `src/superlocalmemory/encoding/entity_resolver.py`

In `_create_entity()`, change the scope of newly created entities:

```python
def _create_entity(self, name, profile_id, entity_type=None):
    etype = entity_type or _guess_entity_type(name)
    now = _now()
    entity = CanonicalEntity(
        entity_id=_new_id(),
        profile_id=profile_id,
        canonical_name=name,
        entity_type=etype,
        first_seen=now,
        last_seen=now,
        fact_count=0,
        scope="global",  # Phase 3: new entities are global by default
    )
```

Note: `CanonicalEntity` already has a `scope` field (added in Phase 1). The `_create_entity()` method currently passes `scope` from the parent fact or defaults to "personal". Phase 3 changes the default to "global".

### 4. Entity aliases also reference global entities

The existing `_alias_lookup()` and `_fuzzy_match()` methods query `canonical_entities WHERE profile_id = ?`. These won't find global entities because global entities have `profile_id` set to the creator's ID but `scope='global'`.

The fix: update `_alias_lookup()` and `_fuzzy_match()` to also consider global-scope entities. The simplest approach is to add `OR ce.scope = 'global'` to the WHERE clause:

```python
# _alias_lookup: change WHERE clause
"WHERE LOWER(ea.alias) = LOWER(?) AND (ce.profile_id = ? OR ce.scope = 'global')"
```

```python
# _fuzzy_match: change WHERE clause for canonical names
"WHERE profile_id = ? OR scope = 'global'"
```

Same for alias fuzzy match:
```python
"WHERE ce.profile_id = ? OR ce.scope = 'global'"
```

This ensures existing aliases and fuzzy matches can find global entities.

---

## What Stays Unchanged

- **No schema changes** — `canonical_entities` already has `scope` column from Phase 1
- **No migration** — existing personal entities work as before
- **No new tables** — `entity_aliases` table already exists and works as-is
- **`store_pipeline.py`** — no changes (entity_resolver handles everything)
- **`recall_pipeline.py`** — no changes (facts already reference entity IDs, which now point to global entities)
- **MCP tools** — no changes
- **Phase 2 domain tags** — automatically shared because all agents reference the same entity IDs

---

## Edge Cases

### Edge Case 1: Agent has existing personal "React" entity

Agent zhihui already has a personal-scope "React" entity from before Phase 3. After Phase 3:
- Tier 0: finds global "React" → uses it
- The old personal "React" entity remains in DB but is no longer referenced by new facts
- Old facts still reference the personal entity ID — they continue to work

### Edge Case 2: Global entity created by agent B

Agent xiaoming creates "Celery" in global scope. Agent zhihui later stores a fact about Celery:
- Tier 0: finds global "Celery" (created by xiaoming) → uses it
- The `profile_id` on the global entity is "xiaoming" (creator), but `scope='global'` makes it visible to all
- This is correct — entity ownership is separate from visibility

### Edge Case 3: Fuzzy match across scopes

Agent zhihui mentions "ReactJS" (alias of "React"):
- Tier 0: exact global lookup for "ReactJS" → not found
- Tier a: personal exact → not found
- Tier b: alias lookup now includes global scope → finds alias "ReactJS" → "React" global entity
- Result: resolved to global "React"

### Edge Case 4: Personal-only entity (agent-specific concept)

An agent mentions "My Secret Project Name" — this is agent-specific:
- Tier 0: global lookup → not found
- Tier a: personal lookup → not found
- Tier b/c/d: → not found
- Create new: scope defaults to "global"

Wait — this means personal-only concepts also become global. Is this a problem?

In practice, for the Hermes agent team (single human user, flat team), this is fine. All entities should be shared. If a truly personal entity is needed in the future, the store caller can explicitly pass `scope="personal"`.

---

## Testing Strategy

| Category | Test | Validates |
|----------|------|-----------|
| Unit | `resolve()` finds global entity before personal | Tier 0 works |
| Unit | `resolve()` creates new entity in global scope | Default scope changed |
| Unit | `resolve()` global entity found by different agent | Cross-agent sharing |
| Unit | `get_entity_by_name()` with scope param | DB method works |
| Unit | `_alias_lookup()` finds global scope alias | Alias cross-scope |
| Unit | `_fuzzy_match()` finds global scope entity | Fuzzy cross-scope |
| Integration | Agent A creates entity, Agent B reuses it | E2E cross-agent |
| Integration | Existing personal entities still work | Backward compatibility |
| Integration | Domain tags shared via global entity | Phase 2 integration |

---

## Effort Estimate

| Module | Lines |
|--------|-------|
| `get_entity_by_name()` scope parameter | ~5 |
| `resolve()` Tier 0 global lookup | ~8 |
| `_create_entity()` default scope | ~1 |
| `_alias_lookup()` global scope inclusion | ~3 |
| `_fuzzy_match()` global scope inclusion | ~3 |
| Tests | ~80 |
| **Total** | **~100** |

This is significantly less than the roadmap estimate of ~400 lines because we reuse the existing `scope` column instead of introducing a new `entity_aliases` reference layer.
