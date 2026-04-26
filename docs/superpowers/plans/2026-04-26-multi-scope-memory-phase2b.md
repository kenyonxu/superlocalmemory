# Phase 2B: LLM-Based Domain Classification Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM fallback for entity→domain classification when rule-based lookup misses, caching results for future rule-based hits.

**Architecture:** Extend the Phase 2 store pipeline so `enrich_fact()` identifies unmapped entities via `get_unmapped_entities()`, classifies each with LLM, caches in `domain_mapping`, then re-resolves. Mode A skips entirely; Mode B/C get automatic coverage growth.

**Tech Stack:** Python 3.11+, SQLite, LLMBackbone (Ollama/OpenAI/Anthropic)

**Spec:** `docs/superpowers/specs/2026-04-26-multi-scope-memory-phase2b-design.md`

---

## Chunk 1: DB Methods + Seed Constant (Tasks 1-3)

### Task 1: KNOWN_DOMAINS constant + `get_unmapped_entities()` DB method

**Files:**

- Modify: `src/superlocalmemory/storage/seed_domain_mapping.py`
- Modify: `src/superlocalmemory/storage/database.py:125-135`
- Test: `tests/test_domain_tags_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_domain_tags_db.py — add inside existing test class or at module level

def test_known_domains_constant():
    """KNOWN_DOMAINS contains the 5 expected domains."""
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS
    assert set(KNOWN_DOMAINS) == {"frontend", "backend", "devops", "mobile", "data"}


def test_get_unmapped_entities_partial(in_memory_db):
    """get_unmapped_entities returns only entities with no domain_mapping row."""
    from superlocalmemory.storage.database import DatabaseManager

    # Seed one mapping
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')"
    )
    in_memory_db.commit()

    unmapped = DatabaseManager(in_memory_db).get_unmapped_entities(
        ["React", "Celery", "Prisma"]
    )
    assert unmapped == ["Celery", "Prisma"]


def test_get_unmapped_entities_all_mapped(in_memory_db):
    """All entities mapped returns empty list."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')"
    )
    in_memory_db.commit()

    unmapped = DatabaseManager(in_memory_db).get_unmapped_entities(["React"])
    assert unmapped == []


def test_get_unmapped_entities_empty_input(in_memory_db):
    """Empty input returns empty list."""
    from superlocalmemory.storage.database import DatabaseManager

    unmapped = DatabaseManager(in_memory_db).get_unmapped_entities([])
    assert unmapped == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_domain_tags_db.py::test_known_domains_constant tests/test_domain_tags_db.py::test_get_unmapped_entities_partial tests/test_domain_tags_db.py::test_get_unmapped_entities_all_mapped tests/test_domain_tags_db.py::test_get_unmapped_entities_empty_input -v`
Expected: FAIL — `KNOWN_DOMAINS` not defined, `get_unmapped_entities` not defined.

- [ ] **Step 3: Implement**

In `src/superlocalmemory/storage/seed_domain_mapping.py`, add after `SEED_DOMAIN_MAPPINGS`:

```python
KNOWN_DOMAINS: list[str] = ["frontend", "backend", "devops", "mobile", "data"]
```

In `src/superlocalmemory/storage/database.py`, add after `resolve_domain_tags()` (line 135):

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

Note: `DatabaseManager.__init__` accepts a `db_path` or an existing connection. Tests should use `DatabaseManager(in_memory_db)` where `in_memory_db` is the raw sqlite3 connection fixture. Check the existing test pattern — if `DatabaseManager` constructor doesn't accept a raw connection, use an in-memory path like `":memory:"` or adapt to match existing test helpers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_domain_tags_db.py::test_known_domains_constant tests/test_domain_tags_db.py::test_get_unmapped_entities_partial tests/test_domain_tags_db.py::test_get_unmapped_entities_all_mapped tests/test_domain_tags_db.py::test_get_unmapped_entities_empty_input -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/storage/seed_domain_mapping.py src/superlocalmemory/storage/database.py tests/test_domain_tags_db.py
git commit -m "feat(phase2b): add KNOWN_DOMAINS constant + get_unmapped_entities()"
```

---

### Task 2: `classify_and_cache_domain()` DB method

**Files:**

- Modify: `src/superlocalmemory/storage/database.py`
- Test: `tests/test_domain_tags_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_domain_tags_db.py

def test_classify_and_cache_valid_domain(in_memory_db, mock_llm_backend):
    """LLM returns valid domain → cached in domain_mapping, returned."""
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm_backend.response = "backend"
    db = DatabaseManager(in_memory_db)

    result = db.classify_and_cache_domain("Celery", mock_llm_backend, KNOWN_DOMAINS)
    assert result == "backend"

    # Verify cached
    rows = in_memory_db.execute(
        "SELECT domain FROM domain_mapping WHERE entity_name = 'Celery'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["domain"] == "backend"


def test_classify_and_cache_unknown_response(in_memory_db, mock_llm_backend):
    """LLM returns 'unknown' → not cached, returns None."""
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm_backend.response = "unknown"
    db = DatabaseManager(in_memory_db)

    result = db.classify_and_cache_domain("Celery", mock_llm_backend, KNOWN_DOMAINS)
    assert result is None

    rows = in_memory_db.execute(
        "SELECT domain FROM domain_mapping WHERE entity_name = 'Celery'"
    ).fetchall()
    assert len(rows) == 0


def test_classify_and_cache_garbage_response(in_memory_db, mock_llm_backend):
    """LLM returns garbage → not cached, returns None."""
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm_backend.response = "I think this is a backend tool"
    db = DatabaseManager(in_memory_db)

    result = db.classify_and_cache_domain("Celery", mock_llm_backend, KNOWN_DOMAINS)
    assert result is None


def test_classify_and_cache_llm_exception(in_memory_db, mock_llm_backend, caplog):
    """LLM raises exception → returns None, logs warning."""
    import logging
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm_backend.should_raise = True
    db = DatabaseManager(in_memory_db)

    with caplog.at_level(logging.WARNING):
        result = db.classify_and_cache_domain("Celery", mock_llm_backend, KNOWN_DOMAINS)
    assert result is None


def test_classify_and_cache_idempotent(in_memory_db, mock_llm_backend):
    """Second call with same entity skips LLM (already cached)."""
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm_backend.response = "backend"
    db = DatabaseManager(in_memory_db)

    result1 = db.classify_and_cache_domain("Celery", mock_llm_backend, KNOWN_DOMAINS)
    assert result1 == "backend"
    assert mock_llm_backend.call_count == 1

    # Reset call count but keep cached data
    mock_llm_backend.call_count = 0
    result2 = db.classify_and_cache_domain("Celery", mock_llm_backend, KNOWN_DOMAINS)
    assert result2 == "backend"
    assert mock_llm_backend.call_count == 0  # LLM not called — early return from cache
```

The `mock_llm_backend` fixture needs to be created. Add to `tests/test_domain_tags_db.py` or `conftest.py`:

```python
class MockLLMBackbone:
    """Minimal mock for LLMBackbone with controllable responses."""

    def __init__(self):
        self.response = ""
        self.should_raise = False
        self.call_count = 0

    def generate(self, prompt, system="", temperature=None, max_tokens=None):
        self.call_count += 1
        if self.should_raise:
            raise RuntimeError("LLM unavailable")
        return self.response

    def is_available(self):
        return True


@pytest.fixture
def mock_llm_backend():
    return MockLLMBackbone()
```

Note: `classify_and_cache_domain()` should check if the entity already has a mapping in domain_mapping first (early return without calling LLM). This makes the idempotent test pass.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_domain_tags_db.py::test_classify_and_cache_valid_domain tests/test_domain_tags_db.py::test_classify_and_cache_unknown_response tests/test_domain_tags_db.py::test_classify_and_cache_garbage_response tests/test_domain_tags_db.py::test_classify_and_cache_llm_exception tests/test_domain_tags_db.py::test_classify_and_cache_idempotent -v`
Expected: FAIL — method not defined.

- [ ] **Step 3: Implement**

In `src/superlocalmemory/storage/database.py`, add after `get_unmapped_entities()`:

```python
def classify_and_cache_domain(
    self,
    entity_name: str,
    llm: Any,
    known_domains: list[str] | None = None,
) -> str | None:
    """Classify entity via LLM, cache in domain_mapping.

    Returns domain string on success, None on failure or unknown.
    INSERT OR IGNORE ensures idempotency — if the entity already has
    a mapping for the classified domain, the insert is silently skipped.
    Users can correct misclassifications via remove_domain_mapping tool.
    """
    if known_domains is None:
        from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS
        known_domains = KNOWN_DOMAINS

    # Early return: already cached (avoid LLM call)
    existing = self.execute(
        "SELECT domain FROM domain_mapping WHERE entity_name = ?",
        (entity_name,),
    )
    if existing:
        return existing[0]["domain"]

    prompt = (
        f"Classify the following technology entity into a domain category.\n\n"
        f"Entity: {entity_name}\n"
        f"Available domains: {', '.join(known_domains)}\n\n"
        f"Rules:\n"
        f"- Respond with exactly one domain name from the list above.\n"
        f"- If the entity doesn't fit any domain, respond: unknown\n"
        f"- Do not explain. Only output the domain name."
    )
    try:
        response = llm.generate(prompt=prompt, temperature=0.0, max_tokens=20)
    except Exception as exc:
        logger.warning("LLM domain classification failed for '%s': %s", entity_name, exc)
        return None

    domain = response.strip().lower()
    if domain not in known_domains:
        logger.debug("LLM returned unknown domain '%s' for entity '%s'", domain, entity_name)
        return None

    self.execute(
        "INSERT OR IGNORE INTO domain_mapping (entity_name, domain) VALUES (?, ?)",
        (entity_name, domain),
    )
    return domain
```

Requires `from typing import Any` (already imported in database.py). The `logger` is already defined at module level.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_domain_tags_db.py::test_classify_and_cache_valid_domain tests/test_domain_tags_db.py::test_classify_and_cache_unknown_response tests/test_domain_tags_db.py::test_classify_and_cache_garbage_response tests/test_domain_tags_db.py::test_classify_and_cache_llm_exception tests/test_domain_tags_db.py::test_classify_and_cache_idempotent -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/storage/database.py tests/test_domain_tags_db.py
git commit -m "feat(phase2b): add classify_and_cache_domain() with LLM fallback"
```

---

### Task 3: `remove_domain_mapping` MCP tool

**Files:**

- Modify: `src/superlocalmemory/mcp/tools_core.py:548`
- Test: `tests/test_domain_tags_db.py`

- [ ] **Step 1: Write the failing test**

The MCP tool tests are typically integration-level. Since this tool is simple (single DELETE), test at DB level:

```python
# tests/test_domain_tags_db.py

def test_remove_domain_mapping(in_memory_db):
    """Delete a domain_mapping row."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('Celery', 'backend')"
    )
    in_memory_db.commit()

    db = DatabaseManager(in_memory_db)
    # Simulate the MCP tool's logic
    cursor = in_memory_db.execute(
        "DELETE FROM domain_mapping WHERE entity_name = ? AND domain = ?",
        ("Celery", "backend"),
    )
    assert cursor.rowcount == 1

    rows = in_memory_db.execute(
        "SELECT * FROM domain_mapping WHERE entity_name = 'Celery'"
    ).fetchall()
    assert len(rows) == 0


def test_remove_domain_mapping_nonexistent(in_memory_db):
    """Deleting non-existent mapping returns 0 rowcount."""
    cursor = in_memory_db.execute(
        "DELETE FROM domain_mapping WHERE entity_name = ? AND domain = ?",
        ("NonExistent", "frontend"),
    )
    assert cursor.rowcount == 0
```

- [ ] **Step 2: Run tests to verify they pass** (these test SQL logic that already exists)

Run: `pytest tests/test_domain_tags_db.py::test_remove_domain_mapping tests/test_domain_tags_db.py::test_remove_domain_mapping_nonexistent -v`
Expected: PASS (tests SQL against existing table)

- [ ] **Step 3: Implement the MCP tool**

In `src/superlocalmemory/mcp/tools_core.py`, add after `add_domain_mapping` (line 548):

```python
@server.tool(annotations=ToolAnnotations(destructiveHint=True))
async def remove_domain_mapping(entity_name: str, domain: str) -> dict:
    """Remove an entity-to-domain mapping.

    Use this to correct misclassifications from LLM-based domain tagging.
    Example: remove_domain_mapping("Celery", "backend")
    """
    try:
        engine = get_engine()
        cursor = engine._db.execute(
            "DELETE FROM domain_mapping WHERE entity_name = ? AND domain = ?",
            (entity_name, domain),
        )
        engine._db.commit()
        if cursor.rowcount == 0:
            return {"success": False, "error": f"No mapping found for '{entity_name}' -> '{domain}'"}
        return {"success": True, "removed": {"entity_name": entity_name, "domain": domain}}
    except Exception as exc:
        logger.exception("remove_domain_mapping failed")
        return {"success": False, "error": str(exc)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_domain_tags_db.py::test_remove_domain_mapping tests/test_domain_tags_db.py::test_remove_domain_mapping_nonexistent -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/mcp/tools_core.py tests/test_domain_tags_db.py
git commit -m "feat(phase2b): add remove_domain_mapping MCP tool"
```

---

## Chunk 2: Store Pipeline Integration (Tasks 4-5)

### Task 4: Thread `llm` parameter through `engine.py → run_store() → enrich_fact()`

**Files:**

- Modify: `src/superlocalmemory/core/store_pipeline.py:56-65,131-164,277-285`
- Modify: `src/superlocalmemory/core/engine.py:375-407`

- [ ] **Step 1: Add `llm` parameter to `enrich_fact()` signature**

In `src/superlocalmemory/core/store_pipeline.py`, change `enrich_fact()` signature (line 56-65):

```python
def enrich_fact(
    fact: AtomicFact,
    record: MemoryRecord,
    profile_id: str,
    *,
    embedder: Any,
    entity_resolver: Any,
    temporal_parser: Any,
    db: Any = None,
    llm: Any = None,  # Phase 2B: LLM backbone for domain classification
) -> AtomicFact:
```

- [ ] **Step 2: Add LLM fallback logic after `resolve_domain_tags()` call**

In `enrich_fact()`, after the existing Phase 2 domain tags block (after line ~82):

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
                domain_tags = db.resolve_domain_tags(list(canonical.keys()))
```

- [ ] **Step 3: Add `llm` parameter to `run_store()` signature**

In `run_store()` (line 131-164), add parameter:

```python
def run_store(
    content: str,
    profile_id: str,
    ...
    consolidation_engine: Any = None,
    llm: Any = None,  # Phase 2B
) -> list[str]:
```

- [ ] **Step 4: Pass `llm` from `run_store()` to `enrich_fact()`**

At the `enrich_fact()` call site (line 277-285), add:

```python
        fact = enrich_fact(
            fact,
            record,
            profile_id,
            embedder=embedder,
            entity_resolver=entity_resolver,
            temporal_parser=temporal_parser,
            db=db,
            llm=llm,  # Phase 2B
        )
```

- [ ] **Step 5: Pass `llm` from `engine.py` to `run_store()`**

In `src/superlocalmemory/core/engine.py`, at the `run_store()` call (line 375-407), add:

```python
        return run_store(
            ...
            consolidation_engine=self._consolidation_engine,
            llm=self._llm,  # Phase 2B
        )
```

- [ ] **Step 6: Run existing domain tag tests to verify no regressions**

Run: `pytest tests/test_domain_tags_db.py tests/test_domain_tags_integration.py tests/test_scope_integration.py -v`
Expected: All PASS — no behavior change yet (llm=None means Phase 2B code is inert).

- [ ] **Step 7: Commit**

```bash
git add src/superlocalmemory/core/store_pipeline.py src/superlocalmemory/core/engine.py
git commit -m "feat(phase2b): thread llm parameter through store pipeline"
```

---

### Task 5: Integration test — LLM fallback end-to-end

**Files:**

- Test: `tests/test_domain_tags_integration.py`

- [ ] **Step 1: Write integration tests**

```python
# tests/test_domain_tags_integration.py — add at end

def test_llm_classify_on_store(in_memory_db, mock_llm_backend):
    """Store with unmapped entity triggers LLM classification, cached for reuse."""
    from superlocalmemory.core.store_pipeline import enrich_fact
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord, FactType
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    db = DatabaseManager(in_memory_db)
    mock_llm_backend.response = "backend"

    fact = AtomicFact(
        fact_id="f1",
        content="Celery is our task queue",
        fact_type=FactType.SEMANTIC,
        entities=["Celery"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    record = MemoryRecord(
        profile_id="test",
        content="Celery is our task queue",
        session_id="s1",
    )

    # enrich with LLM
    result = enrich_fact(
        fact, record, "test",
        embedder=None, entity_resolver=None, temporal_parser=None,
        db=db, llm=mock_llm_backend,
    )
    assert result.domain_tags == ["backend"]
    assert mock_llm_backend.call_count == 1

    # Second enrich with same entity — LLM should NOT be called (cached)
    mock_llm_backend.call_count = 0
    fact2 = AtomicFact(
        fact_id="f2",
        content="Celery workers are configured",
        fact_type=FactType.SEMANTIC,
        entities=["Celery"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    result2 = enrich_fact(
        fact2, record, "test",
        embedder=None, entity_resolver=None, temporal_parser=None,
        db=db, llm=mock_llm_backend,
    )
    assert result2.domain_tags == ["backend"]
    assert mock_llm_backend.call_count == 0  # cached, no LLM call


def test_llm_partial_match_only_classifies_unmapped(in_memory_db, mock_llm_backend):
    """Partial match: Redis (seed) + Celery (unmapped). Only Celery triggers LLM."""
    from superlocalmemory.core.store_pipeline import enrich_fact
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord, FactType

    db = DatabaseManager(in_memory_db)
    # Redis is in seed data (inserted by M015). For in_memory_db, insert manually.
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('Redis', 'backend')"
    )
    in_memory_db.commit()

    mock_llm_backend.response = "devops"

    fact = AtomicFact(
        fact_id="f3",
        content="Celery uses Redis as broker",
        fact_type=FactType.SEMANTIC,
        entities=["Celery", "Redis"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    record = MemoryRecord(
        profile_id="test",
        content="Celery uses Redis as broker",
        session_id="s1",
    )

    result = enrich_fact(
        fact, record, "test",
        embedder=None, entity_resolver=None, temporal_parser=None,
        db=db, llm=mock_llm_backend,
    )
    assert "backend" in result.domain_tags  # from Redis seed
    assert mock_llm_backend.call_count == 1  # only Celery triggered LLM


def test_llm_not_called_when_no_llm(in_memory_db, mock_llm_backend):
    """llm=None → no LLM calls, domain_tags from rules only."""
    from superlocalmemory.core.store_pipeline import enrich_fact
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord, FactType

    db = DatabaseManager(in_memory_db)
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('Redis', 'backend')"
    )
    in_memory_db.commit()

    fact = AtomicFact(
        fact_id="f4",
        content="Celery uses Redis",
        fact_type=FactType.SEMANTIC,
        entities=["Celery", "Redis"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    record = MemoryRecord(
        profile_id="test",
        content="Celery uses Redis",
        session_id="s1",
    )

    result = enrich_fact(
        fact, record, "test",
        embedder=None, entity_resolver=None, temporal_parser=None,
        db=db, llm=None,  # No LLM
    )
    assert result.domain_tags == ["backend"]  # only Redis seed
    assert mock_llm_backend.call_count == 0  # LLM never called
```

Note: The `mock_llm_backend` fixture must be available. If not already in `conftest.py`, add the `MockLLMBackbone` class and fixture (from Task 2) at the top of `tests/test_domain_tags_integration.py` or in `conftest.py`.

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_domain_tags_integration.py::test_llm_classify_on_store tests/test_domain_tags_integration.py::test_llm_partial_match_only_classifies_unmapped tests/test_domain_tags_integration.py::test_llm_not_called_when_no_llm -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_domain_tags_integration.py
git commit -m "test(phase2b): integration tests for LLM domain classification fallback"
```

---

## Chunk 3: Final Validation (Task 6)

### Task 6: Full test suite + cleanup

**Files:**

- All modified files

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -q --tb=short -x --ignore=tests/test_code_graph --ignore=tests/test_benchmarks`
Expected: All PASS (4 pre-existing failures in migration_runner are known and unrelated).

- [ ] **Step 2: Run ruff lint**

Run: `ruff check src/superlocalmemory/storage/database.py src/superlocalmemory/storage/seed_domain_mapping.py src/superlocalmemory/core/store_pipeline.py src/superlocalmemory/core/engine.py src/superlocalmemory/mcp/tools_core.py`
Expected: No errors.

- [ ] **Step 3: Verify Mode A isolation**

Confirm that with `llm=None` (Mode A), the Phase 2B code paths are completely inert:
- `enrich_fact()` with `llm=None` → `if llm:` guard prevents entry
- No LLM imports at module level (only inside the `if llm:` block)
- `classify_and_cache_domain()` is never called

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore(phase2b): lint cleanup"
```
