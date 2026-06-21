# Multi-Scope Memory Phase 2B Design: LLM-Based Domain Classification

> Phase 2B Spec — Automatic Entity→Domain Classification via LLM
> Date: 2026-04-26
> Builds on: Phase 2 (rule-based domain_mapping + domain_tags)

## Context

### Use Case

Phase 2 uses a rule-based `domain_mapping` table with 48 seed entries to tag entities during store. When an entity isn't in the table, `domain_tags` stays NULL and domain-based sharing is skipped. As the agent team encounters entities beyond the seed list (Celery, Prisma, Bull, project-specific terms), the coverage gap grows.

Phase 2B adds LLM-based classification as a fallback: entities that miss the rule-based lookup are classified by the LLM, and results are cached in `domain_mapping` for future rule-based hits.

### Motivation

- Grow domain_mapping coverage automatically without manual curation
- LLM cost is one-time per entity (cached after first classification)
- Graceful degradation: Mode A users unaffected, Mode B/C get enhanced coverage

### Scope

Phase 2B is limited to:
- LLM fallback in `enrich_fact()` for entities with no domain_mapping row
- New `get_unmapped_entities()` DB method to identify partial misses
- Single-entity LLM calls with known-domain validation
- Caching results in `domain_mapping` table
- `remove_domain_mapping` MCP tool for correcting misclassifications
- `KNOWN_DOMAINS` constant shared between LLM prompt and validation

Out of scope:
- LLM batch classification
- Confidence scoring or uncertain caching
- Domain learning / statistics
- Automatic domain discovery (accepting new domains beyond the known list)

---

## Design Decisions

| Decision | Resolution |
|----------|-----------|
| Domain scope | Only accept known domains: frontend, backend, devops, mobile, data |
| Batch strategy | Per-entity LLM calls (simple, isolated failures) |
| Error handling | Silent degradation + warning log (matches existing LLM feature pattern) |
| Caching | Direct INSERT into `domain_mapping`; same entity never calls LLM again |
| Correction | `remove_domain_mapping` MCP tool for manual fixes |
| LLM model | Uses whatever LLMBackbone is configured (Mode B: local Ollama, Mode C: cloud) |
| Mode A | No LLM calls; domain_tags stays NULL (no behavior change from Phase 2) |

---

## Architecture

### Store Flow (Extended)

```
enrich_fact() after entity resolution:
  canonical = {"Celery": "celery_01", "Redis": "redis_01"}
                    ↓
  db.resolve_domain_tags(["Celery", "Redis"])
                    ↓
  returns ["backend"]  (Redis hits seed, Celery misses)
                    ↓
  db.get_unmapped_entities(["Celery", "Redis"])
                    ↓
  returns ["Celery"]  (only entities with zero rows in domain_mapping)
                    ↓
  Mode B/C + LLM available?
    YES → for each unmapped entity:
            llm_classify("Celery") → "backend"
            validate: "backend" in KNOWN_DOMAINS → OK
            INSERT OR IGNORE INTO domain_mapping ("Celery", "backend")
    NO  → skip (domain_tags stays as-is)
                    ↓
  Re-resolve: db.resolve_domain_tags(["Celery", "Redis"]) → ["backend"]
                    ↓
  domain_tags = ["backend"]
```

### LLM Classification Prompt

```
Classify the following technology entity into a domain category.

Entity: {entity_name}
Available domains: frontend, backend, devops, mobile, data

Rules:
- Respond with exactly one domain name from the list above.
- If the entity doesn't fit any domain, respond: unknown
- Do not explain. Only output the domain name.
```

---

## Code Changes

### 1. Known Domains Constant (~5 lines)

File: `src/superlocalmemory/storage/seed_domain_mapping.py`

```python
KNOWN_DOMAINS: list[str] = ["frontend", "backend", "devops", "mobile", "data"]
```

Added alongside the existing `SEED_DOMAIN_MAPPINGS` list.

### 2. DatabaseManager: Two New Methods (~40 lines)

File: `src/superlocalmemory/storage/database.py`

**`get_unmapped_entities()`** — returns entity names with zero rows in domain_mapping:

```python
def get_unmapped_entities(self, entity_names: list[str]) -> list[str]:
    """Return entity names that have no row in domain_mapping."""
    if not entity_names:
        return []
    placeholders = ",".join("?" * len(entity_names))
    rows = self.execute(
        f"SELECT DISTINCT entity_name FROM domain_mapping "
        f"WHERE entity_name IN ({placeholders})",
        tuple(entity_names),
    )
    mapped = {r["entity_name"] for r in rows}
    return [e for e in entity_names if e not in mapped]
```

**`classify_and_cache_domain()`** — LLM classify + cache:

```python
def classify_and_cache_domain(
    self, entity_name: str, llm: Any, known_domains: list[str] | None = None,
) -> str | None:
    """Use LLM to classify entity, cache result in domain_mapping.

    Returns the domain string if classified successfully, None otherwise.
    INSERT OR IGNORE ensures idempotency — if the entity already has a
    mapping for the classified domain, the insert is silently skipped.
    Users can correct misclassifications via remove_domain_mapping tool.
    """
```

Logic:
1. Generate LLM response with classification prompt
2. Strip/normalize response
3. Check `response in known_domains`
4. If valid: `INSERT OR IGNORE INTO domain_mapping`, return domain
5. If invalid or LLM error: return None, log warning

### 3. Store Pipeline: `enrich_fact()` LLM Fallback (~10 lines)

File: `src/superlocalmemory/core/store_pipeline.py`

After the existing `resolve_domain_tags()` call:

```python
# Phase 2: resolve domain tags from canonical entity names
domain_tags = None
if db and canonical:
    domain_tags = db.resolve_domain_tags(list(canonical.keys()))

    # Phase 2B: LLM fallback for unmapped entities (handles partial matches)
    if llm:
        from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS
        unmapped = db.get_unmapped_entities(list(canonical.keys()))
        for entity_name in unmapped:
            db.classify_and_cache_domain(entity_name, llm, KNOWN_DOMAINS)
        if unmapped:
            # Re-resolve with newly cached mappings
            domain_tags = db.resolve_domain_tags(list(canonical.keys()))
```

`enrich_fact()` signature gains `llm: Any = None` keyword argument, passed from `run_store()`.

### 4. LLM Propagation Chain (~8 lines across 3 files)

The LLM backbone threads through 3 call sites:

1. **`engine.py::store()`** — passes `llm=self._llm` to `run_store()`
2. **`store_pipeline.py::run_store()`** — gains `llm: Any = None` parameter, passes to `enrich_fact()`
3. **`store_pipeline.py::enrich_fact()`** — gains `llm: Any = None` parameter

Engine already has `self._llm` initialized in `_init_heavy_layer()`. The LLM is None in Mode A and LIGHT capabilities, so the `if llm:` guard in enrich_fact() naturally skips classification.

### 5. MCP Tool: `remove_domain_mapping` (~15 lines)

File: `src/superlocalmemory/mcp/tools_core.py`

```python
@server.tool(annotations=ToolAnnotations(destructiveHint=True))
async def remove_domain_mapping(entity_name: str, domain: str) -> dict:
    """Remove an entity-to-domain mapping.

    Use this to correct LLM misclassifications.
    Example: remove_domain_mapping("Celery", "backend")
    """
```

Logic: `DELETE FROM domain_mapping WHERE entity_name=? AND domain=?`. Returns `success=False` if 0 rows deleted (mapping didn't exist).

### 6. `classify_and_cache_domain` LLM Prompt

The method constructs the prompt internally:

```python
prompt = (
    f"Classify the following technology entity into a domain category.\n\n"
    f"Entity: {entity_name}\n"
    f"Available domains: {', '.join(known_domains)}\n\n"
    f"Rules:\n"
    f"- Respond with exactly one domain name from the list above.\n"
    f"- If the entity doesn't fit any domain, respond: unknown\n"
    f"- Do not explain. Only output the domain name."
)
result = llm.generate(prompt=prompt, temperature=0.0, max_tokens=20)
```

---

## Testing Strategy

| Category | Test | Validates |
|----------|------|-----------|
| Unit | `get_unmapped_entities` with partial match | Returns only unmapped entity names |
| Unit | `get_unmapped_entities` with all mapped | Returns empty list |
| Unit | `get_unmapped_entities` with empty input | Returns empty list |
| Unit | `classify_and_cache_domain` with mock LLM returning valid domain | Inserts into domain_mapping, returns domain |
| Unit | `classify_and_cache_domain` with mock LLM returning unknown | Returns None, no insert |
| Unit | `classify_and_cache_domain` with mock LLM returning garbage | Returns None, no insert |
| Unit | `classify_and_cache_domain` with LLM raising exception | Returns None, logged warning |
| Unit | `enrich_fact` with LLM fallback on partial match | Only unmapped entities trigger LLM |
| Unit | `enrich_fact` with no LLM skips classification | domain_tags stays as rule-based result |
| Unit | `enrich_fact` all entities already mapped | LLM never called |
| Integration | Store → LLM classify → recall with domain overlap | E2E domain sharing after LLM classification |
| MCP | `remove_domain_mapping` removes entry | Subsequent resolve returns empty |
| MCP | `remove_domain_mapping` non-existent entry | Returns success=False gracefully |

---

## Effort Estimate

| Module | Lines |
|--------|-------|
| KNOWN_DOMAINS constant | ~3 |
| `get_unmapped_entities()` | ~12 |
| `classify_and_cache_domain()` | ~25 |
| `enrich_fact()` LLM fallback | ~10 |
| LLM propagation chain (engine → run_store → enrich_fact) | ~8 |
| `remove_domain_mapping` MCP tool | ~15 |
| Tests | ~100 |
| **Total** | **~173** |

---

## Future Phases (Out of Scope)

### Phase 2C: Domain Learning
- Track classification accuracy (user corrections via remove_domain_mapping)
- Suggest new domains when "unknown" rate exceeds threshold

### Phase 3: Global Authoritative Entities
- Global `canonical_entities` as authority
- Cross-scope entity disambiguation during store
