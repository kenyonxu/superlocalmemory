# Phase 3: Global Authoritative Entities Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make entity resolution global-first so all agents share the same canonical entities, eliminating duplicates.

**Architecture:** Add Tier 0 to `EntityResolver.resolve()` that checks global scope before personal. New entities default to `scope='global'`. Reuse existing `canonical_entities.scope` column — zero schema changes, zero migrations.

**Tech Stack:** Python 3.11+, SQLite

**Spec:** `docs/superpowers/specs/2026-04-26-multi-scope-memory-phase3-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/superlocalmemory/encoding/entity_resolver.py` | All Phase 3 changes: Tier 0, global helper, default scope |
| `tests/test_global_entities.py` | New test file for Phase 3 |

No other files need modification.

---

## Chunk 1: Core Implementation (Tasks 1-3)

### Task 1: `_get_global_entity()` helper + `resolve()` Tier 0

**Files:**

- Modify: `src/superlocalmemory/encoding/entity_resolver.py:244-331` (resolve method)
- Test: `tests/test_global_entities.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_global_entities.py
"""Phase 3 tests — global authoritative entities."""

from __future__ import annotations

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import CanonicalEntity, _new_id, _now
from superlocalmemory.encoding.entity_resolver import EntityResolver


@pytest.fixture
def db_with_global_entity(tmp_path):
    """DB with a global-scope 'React' entity already created."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="creator_agent",
        scope="global",
        canonical_name="React",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))
    return db, entity_id


def test_global_entity_found_before_personal(db_with_global_entity):
    """Tier 0: resolve() finds global entity before checking personal scope."""
    db, global_id = db_with_global_entity
    resolver = EntityResolver(db)

    result = resolver.resolve(["React"], profile_id="other_agent")
    assert "React" in result
    assert result["React"] == global_id


def test_global_entity_shared_across_agents(db_with_global_entity):
    """Different agents resolve to the same global entity ID."""
    db, global_id = db_with_global_entity
    resolver = EntityResolver(db)

    r1 = resolver.resolve(["React"], profile_id="agent_a")
    r2 = resolver.resolve(["React"], profile_id="agent_b")
    assert r1["React"] == global_id
    assert r2["React"] == global_id


def test_no_global_entity_falls_back_to_personal(tmp_path):
    """No global entity → falls back to existing personal entity (Tier a)."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="agent_a",
        scope="personal",
        canonical_name="MySecretProject",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))

    resolver = EntityResolver(db)
    result = resolver.resolve(["MySecretProject"], profile_id="agent_a")
    assert result["MySecretProject"] == entity_id


def test_no_match_creates_global_entity(tmp_path):
    """No existing entity → creates new one in global scope."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)

    resolver = EntityResolver(db)
    result = resolver.resolve(["Celery"], profile_id="agent_a")
    assert "Celery" in result
    new_id = result["Celery"]

    # Verify the new entity is global scope
    rows = db.execute(
        "SELECT scope, canonical_name FROM canonical_entities WHERE entity_id = ?",
        (new_id,),
    )
    assert len(rows) == 1
    assert rows[0]["scope"] == "global"
    assert rows[0]["canonical_name"] == "Celery"


def test_global_lookup_case_insensitive(db_with_global_entity):
    """Global lookup is case-insensitive (same as existing personal lookup)."""
    db, global_id = db_with_global_entity
    resolver = EntityResolver(db)

    result = resolver.resolve(["react", "REACT"], profile_id="agent_a")
    assert result["react"] == global_id
    assert result["REACT"] == global_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_global_entities.py -v --tb=short`
Expected: FAIL — `_get_global_entity` not defined, new entities have `scope='personal'`.

- [ ] **Step 3: Implement `_get_global_entity()` and Tier 0**

In `src/superlocalmemory/encoding/entity_resolver.py`, add `import json` to the existing imports at the top (needed for the helper).

Add `_get_global_entity()` method after `_touch_last_seen()` (line ~567):

```python
def _get_global_entity(self, name: str) -> CanonicalEntity | None:
    """Look up entity in global scope only (Phase 3)."""
    rows = self._db.execute(
        "SELECT * FROM canonical_entities "
        "WHERE LOWER(canonical_name) = LOWER(?) AND scope = 'global' "
        "LIMIT 1",
        (name,),
    )
    if not rows:
        return None
    d = dict(rows[0])
    return CanonicalEntity(
        entity_id=d["entity_id"],
        profile_id=d["profile_id"],
        scope=d.get("scope", "personal"),
        shared_with=json.loads(d["shared_with"]) if d.get("shared_with") else None,
        canonical_name=d["canonical_name"],
        entity_type=d["entity_type"],
        first_seen=d["first_seen"],
        last_seen=d["last_seen"],
        fact_count=d.get("fact_count", 0),
    )
```

Add Tier 0 in `resolve()`, after the stop-word/length filter block (after line ~276 `if re.match(r"^[\d.v\-/]+$", name): continue`) and before the existing `# Tier a:` comment (line ~282):

```python
            # Tier 0 (Phase 3): global authoritative entity lookup
            global_entity = self._get_global_entity(name)
            if global_entity is not None:
                resolution[raw] = global_entity.entity_id
                self._touch_last_seen(global_entity.entity_id)
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_global_entities.py -v --tb=short`
Expected: `test_no_match_creates_global_entity` still fails (new entities default to "personal").

- [ ] **Step 5: Commit partial progress**

```bash
git add src/superlocalmemory/encoding/entity_resolver.py tests/test_global_entities.py
git commit -m "feat(phase3): add Tier 0 global entity lookup + tests"
```

---

### Task 2: New entities default to `scope='global'`

**Files:**

- Modify: `src/superlocalmemory/encoding/entity_resolver.py:508-535` (_create_entity)

- [ ] **Step 1: Modify `_create_entity()` default scope**

In `src/superlocalmemory/encoding/entity_resolver.py`, change `_create_entity()` (line ~516):

Current code:
```python
        entity = CanonicalEntity(
            entity_id=_new_id(),
            profile_id=profile_id,
            canonical_name=name,
            entity_type=etype,
            first_seen=now,
            last_seen=now,
            fact_count=0,
        )
```

Change to:
```python
        entity = CanonicalEntity(
            entity_id=_new_id(),
            profile_id=profile_id,
            scope="global",
            canonical_name=name,
            entity_type=etype,
            first_seen=now,
            last_seen=now,
            fact_count=0,
        )
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_global_entities.py -v --tb=short`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add src/superlocalmemory/encoding/entity_resolver.py
git commit -m "feat(phase3): new entities default to scope='global'"
```

---

### Task 3: `_alias_lookup()` and `_fuzzy_match()` include global scope

**Files:**

- Modify: `src/superlocalmemory/encoding/entity_resolver.py:454-504`
- Test: `tests/test_global_entities.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_global_entities.py`:

```python
def test_alias_lookup_finds_global_entity(tmp_path):
    """_alias_lookup finds global entity via alias (cross-scope)."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="creator",
        scope="global",
        canonical_name="React",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))
    from superlocalmemory.storage.models import EntityAlias
    db.store_alias(EntityAlias(
        alias_id=_new_id(),
        entity_id=entity_id,
        alias="ReactJS",
        confidence=0.9,
        source="manual",
    ))

    resolver = EntityResolver(db)
    # Tier 0 exact match won't find "ReactJS" (different name)
    # But Tier b (alias) should find it via global scope
    result = resolver.resolve(["ReactJS"], profile_id="other_agent")
    assert "ReactJS" in result
    assert result["ReactJS"] == entity_id


def test_fuzzy_match_finds_global_entity(tmp_path):
    """_fuzzy_match finds global entity (cross-scope fuzzy match)."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="creator",
        scope="global",
        canonical_name="Kubernetes",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))

    resolver = EntityResolver(db)
    # "Kuberntes" (typo) should fuzzy-match to "Kubernetes" global entity
    # Tier 0 won't match (exact name differs)
    # Tier c (fuzzy) should find it
    result = resolver.resolve(["Kuberntes"], profile_id="other_agent")
    assert "Kuberntes" in result
    assert result["Kuberntes"] == entity_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_global_entities.py::test_alias_lookup_finds_global_entity tests/test_global_entities.py::test_fuzzy_match_finds_global_entity -v --tb=short`
Expected: FAIL — alias and fuzzy queries only look at personal profile_id.

- [ ] **Step 3: Implement global scope in `_alias_lookup()` and `_fuzzy_match()`**

In `_alias_lookup()` (line ~454), change the WHERE clause:

```python
    def _alias_lookup(self, name: str, profile_id: str) -> str | None:
        """Look up entity_id via alias table (case-insensitive)."""
        rows = self._db.execute(
            "SELECT ea.entity_id FROM entity_aliases ea "
            "JOIN canonical_entities ce ON ce.entity_id = ea.entity_id "
            "WHERE LOWER(ea.alias) = LOWER(?) AND (ce.profile_id = ? OR ce.scope = 'global')",
            (name, profile_id),
        )
```

In `_fuzzy_match()` canonical names query (line ~478):

```python
        # Check canonical names
        rows = self._db.execute(
            "SELECT entity_id, canonical_name FROM canonical_entities "
            "WHERE profile_id = ? OR scope = 'global'",
            (profile_id,),
        )
```

In `_fuzzy_match()` aliases query (line ~491):

```python
        # Check aliases
        alias_rows = self._db.execute(
            "SELECT ea.entity_id, ea.alias FROM entity_aliases ea "
            "JOIN canonical_entities ce ON ce.entity_id = ea.entity_id "
            "WHERE ce.profile_id = ? OR ce.scope = 'global'",
            (profile_id,),
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_global_entities.py -v --tb=short`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/encoding/entity_resolver.py tests/test_global_entities.py
git commit -m "feat(phase3): alias/fuzzy lookup includes global scope"
```

---

## Chunk 2: Validation (Task 4)

### Task 4: Full test suite + lint

- [ ] **Step 1: Run ruff lint**

Run: `ruff check src/superlocalmemory/encoding/entity_resolver.py`
Expected: No errors.

- [ ] **Step 2: Run full scope/domain/entity test suite**

Run: `pytest tests/test_global_entities.py tests/test_domain_tags_db.py tests/test_domain_tags_integration.py tests/test_scope_db.py tests/test_scope_integration.py tests/test_scope_schema.py -v --tb=short`
Expected: All PASS.

- [ ] **Step 3: Run broader test suite for regressions**

Run: `pytest tests/ -q --tb=short -x --ignore=tests/test_code_graph --ignore=tests/test_benchmarks --ignore=tests/test_integration`
Expected: All PASS (4 pre-existing migration_runner failures are known and unrelated).

- [ ] **Step 4: Final commit if any cleanup needed**
