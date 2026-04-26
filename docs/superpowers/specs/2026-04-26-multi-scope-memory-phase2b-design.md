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
- LLM fallback in `enrich_fact()` when `resolve_domain_tags()` returns empty
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
  unresolved = ["Celery"]  (entities whose names produced no domain tag)
                    ↓
  Mode B/C + LLM available?
    YES → for each unresolved entity:
            llm_classify("Celery") → "backend"
            validate: "backend" in KNOWN_DOMAINS → OK
            INSERT INTO domain_mapping ("Celery", "backend")
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

### 2. DatabaseManager: `classify_and_cache_domain()` (~25 lines)

File: `src/superlocalmemory/storage/database.py`

```python
def classify_and_cache_domain(
    self, entity_name: str, llm: Any, known_domains: list[str] | None = None,
) -> str | None:
    """Use LLM to classify entity, cache result in domain_mapping.

    Returns the domain string if classified successfully, None otherwise.
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

    # Phase 2B: LLM fallback for unresolved entities
    if llm and not domain_tags and canonical:
        from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS
        for entity_name in canonical:
            result = db.classify_and_cache_domain(entity_name, llm, KNOWN_DOMAINS)
        # Re-resolve with newly cached mappings
        domain_tags = db.resolve_domain_tags(list(canonical.keys()))
```

`enrich_fact()` signature gains `llm: Any = None` keyword argument, passed from `run_store()`.

### 4. `run_store()` LLM Propagation (~3 lines)

File: `src/superlocalmemory/core/store_pipeline.py`

`run_store()` already has no direct LLM parameter but has access to `config`. The LLM backbone is on the engine. Add `llm: Any = None` to `run_store()` signature, pass through to `enrich_fact()`.

Engine already has `self._llm` initialized in `_init_heavy_layer()`. Pass it to `run_store()` call in `engine.py`.

### 5. MCP Tool: `remove_domain_mapping` (~15 lines)

File: `src/superlocalmemory/mcp/tools_core.py`

```python
@server.tool()
async def remove_domain_mapping(entity_name: str, domain: str) -> dict:
    """Remove an entity-to-domain mapping.

    Use this to correct LLM misclassifications.
    Example: remove_domain_mapping("Celery", "frontend")
    """
```

Logic: `DELETE FROM domain_mapping WHERE entity_name=? AND domain=?`

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
| Unit | `classify_and_cache_domain` with mock LLM returning valid domain | Inserts into domain_mapping, returns domain |
| Unit | `classify_and_cache_domain` with mock LLM returning unknown | Returns None, no insert |
| Unit | `classify_and_cache_domain` with mock LLM returning garbage | Returns None, no insert |
| Unit | `classify_and_cache_domain` with LLM raising exception | Returns None, logged warning |
| Unit | `enrich_fact` with LLM fallback triggers classification | domain_tags populated after LLM call |
| Unit | `enrich_fact` with no LLM skips classification | domain_tags stays None |
| Unit | `enrich_fact` rule hit skips LLM | LLM never called when rule matches |
| Integration | Store → LLM classify → recall with domain overlap | E2E domain sharing after LLM classification |
| MCP | `remove_domain_mapping` removes entry | Subsequent resolve returns empty |
| MCP | `remove_domain_mapping` non-existent entry | Returns success=False gracefully |

---

## Effort Estimate

| Module | Lines |
|--------|-------|
| KNOWN_DOMAINS constant | ~3 |
| `classify_and_cache_domain()` | ~25 |
| `enrich_fact()` LLM fallback | ~10 |
| `run_store()` + `engine.py` LLM propagation | ~5 |
| `remove_domain_mapping` MCP tool | ~15 |
| Tests | ~80 |
| **Total** | **~138** |

---

## Future Phases (Out of Scope)

### Phase 2C: Domain Learning
- Track classification accuracy (user corrections via remove_domain_mapping)
- Suggest new domains when "unknown" rate exceeds threshold

### Phase 3: Global Authoritative Entities
- Global `canonical_entities` as authority
- Cross-scope entity disambiguation during store
