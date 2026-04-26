za# Multi-Scope Memory Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add skill-domain tags to SuperLocalMemory — agents declare skill domains, memories get auto-tagged at store time, and recall-time domain matching enables emergent knowledge sharing without manual `shared_with` pairing.

**Architecture:** New `domain_mapping` table maps entity names to domains. Four core tables gain a `domain_tags` JSON column. `DatabaseManager.resolve_domain_tags()` does batch lookup. `_scope_where()` gains domain-overlap matching. Store pipeline resolves tags after entity resolution. Recall pipeline propagates agent `skill_tags` through channels.

**Tech Stack:** Python 3.11+, SQLite, existing SLM retrieval infrastructure

**Spec:** `docs/superpowers/specs/2026-04-25-multi-scope-memory-phase2-design.md`

### Test Strategy

每个 Task 只跑该 Task 相关的测试（几秒）。Chunk 结束时跑相关子系统（~30秒）。全量回归只在 Task 11 跑一次（~15分钟）。

```bash
# 每个 Task: 只跑新增测试 + 直接相关文件
PHASE2_TESTS="tests/test_domain_tags_schema.py tests/test_domain_tags_db.py tests/test_domain_tags_integration.py"

# 每个 Chunk 结束: 相关子系统
CHUNK1_TESTS="tests/test_domain_tags_schema.py tests/test_domain_tags_db.py tests/test_storage/test_migration_runner.py tests/test_scope_integration.py"
CHUNK2_TESTS="tests/test_domain_tags_db.py tests/test_storage/ tests/test_retrieval/"
CHUNK3_TESTS="$PHASE2_TESTS tests/test_scope_integration.py"

# Task 11 最终验证: 全量（排除预存在的 code_graph failures）
pytest tests/ -q --ignore=tests/test_code_graph --ignore=tests/test_integration -m "not slow and not ollama and not benchmark"
```

---

## Chunk 1: Foundation — Schema, Models, Migration, DatabaseManager

### Task 1: Add domain_mapping table and domain_tags column to schema.py

**Files:**
- Modify: `src/superlocalmemory/storage/schema.py`
- Test: `tests/test_domain_tags_schema.py` (create)

- [ ] **Step 1: Write failing test for domain_mapping table and domain_tags columns**

```python
# tests/test_domain_tags_schema.py
"""Verify domain_mapping table and domain_tags columns exist after schema creation."""
import sqlite3
import pytest

DOMAIN_TAGS_TABLES = [
    "atomic_facts",
    "canonical_entities",
    "graph_edges",
    "temporal_events",
]


@pytest.fixture
def fresh_db():
    from superlocalmemory.storage import schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.create_all_tables(conn)
    conn.commit()
    yield conn
    conn.close()


def test_domain_mapping_table_exists(fresh_db):
    """domain_mapping table must exist after schema creation."""
    rows = fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='domain_mapping'"
    ).fetchall()
    assert len(rows) == 1


def test_domain_mapping_columns(fresh_db):
    """domain_mapping must have entity_name and domain columns."""
    rows = fresh_db.execute("PRAGMA table_info(domain_mapping)").fetchall()
    col_names = {r["name"] for r in rows}
    assert "entity_name" in col_names
    assert "domain" in col_names


def test_domain_mapping_primary_key(fresh_db):
    """domain_mapping PK must be (entity_name, domain)."""
    rows = fresh_db.execute("PRAGMA table_info(domain_mapping)").fetchall()
    pk_cols = {r["name"] for r in rows if r["pk"] > 0}
    assert pk_cols == {"entity_name", "domain"}


@pytest.mark.parametrize("table", DOMAIN_TAGS_TABLES)
def test_domain_tags_column_exists(fresh_db, table):
    """Each of the 4 core tables must have a domain_tags column."""
    rows = fresh_db.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = {r["name"] for r in rows}
    assert "domain_tags" in col_names, f"{table} missing 'domain_tags'. Got: {col_names}"


def test_domain_tags_default_is_null(fresh_db):
    """New rows default to domain_tags=NULL."""
    fresh_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content) "
        "VALUES ('f1', 'm1', 'alice', 'test')"
    )
    row = fresh_db.execute(
        "SELECT domain_tags FROM atomic_facts WHERE fact_id='f1'"
    ).fetchone()
    assert row["domain_tags"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_domain_tags_schema.py -v`
Expected: FAIL — domain_mapping table and domain_tags column don't exist yet

- [ ] **Step 3: Add domain_mapping DDL and domain_tags columns to schema.py**

In `src/superlocalmemory/storage/schema.py`, add a new `_SQL_DOMAIN_MAPPING` constant after the existing table SQL constants (e.g., after `_SQL_ATOMIC_FACTS`):

```python
_SQL_DOMAIN_MAPPING = """\
CREATE TABLE IF NOT EXISTS domain_mapping (
    entity_name TEXT NOT NULL,
    domain      TEXT NOT NULL,
    PRIMARY KEY (entity_name, domain)
);
"""
```

Add `domain_tags TEXT` to each of these four CREATE TABLE statements:
- `_SQL_ATOMIC_FACTS` — add `domain_tags TEXT,` after `shared_with TEXT,` line
- `_SQL_CANONICAL_ENTITIES` — add `domain_tags TEXT,` after `shared_with TEXT,` line
- `_SQL_GRAPH_EDGES` — add `domain_tags TEXT,` after `shared_with TEXT,` line
- `_SQL_TEMPORAL_EVENTS` — add `domain_tags TEXT,` after `shared_with TEXT,` line

Add `_SQL_DOMAIN_MAPPING` to the `_DDL_ORDERED` tuple (at line ~694), after `_SQL_CONFIG` and before `_SQL_V2_MIGRATION_CLEANUP`:

```python
    _SQL_CONFIG,
    _SQL_DOMAIN_MAPPING,   # Phase 2: entity→domain mapping
    # V2 migration cleanup ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_domain_tags_schema.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_domain_tags_schema.py src/superlocalmemory/storage/schema.py
git commit -m "feat(phase2): domain_mapping table + domain_tags column in schema"
```

---

### Task 2: Add domain_tags to data models

**Files:**
- Modify: `src/superlocalmemory/storage/models.py`

- [ ] **Step 1: Add domain_tags field to AtomicFact and Profile.skill_tags property**

In `src/superlocalmemory/storage/models.py`:

Add `domain_tags` field to `AtomicFact` (at line ~145, after `shared_with`):

```python
    scope: str = "personal"
    shared_with: list[str] | None = None
    domain_tags: list[str] | None = None        # Phase 2: resolved domain tags
```

Add `skill_tags` property to `Profile` (after the `config` field at line ~110):

```python
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def skill_tags(self) -> list[str]:
        return self.config.get("skill_tags", [])
```

- [ ] **Step 2: Run existing model tests to verify no regressions**

Run: `pytest tests/test_storage/test_models.py -q --tb=short 2>/dev/null || echo "No model tests found — skip"`
Expected: No failures (or no test file — that's fine too)

- [ ] **Step 3: Commit**

```bash
git add src/superlocalmemory/storage/models.py
git commit -m "feat(phase2): domain_tags on AtomicFact, skill_tags property on Profile"
```

---

### Task 3: M015 migration — domain_mapping + domain_tags + seed data

**Files:**
- Create: `src/superlocalmemory/storage/migrations/M015_add_domain_tags.py`
- Create: `src/superlocalmemory/storage/seed_domain_mapping.py`
- Modify: `src/superlocalmemory/storage/migration_runner.py` (register M015)

- [ ] **Step 1: Write seed data file**

Create `src/superlocalmemory/storage/seed_domain_mapping.py`:

```python
"""Phase 2 seed data: entity_name → domain mappings (~50 entries)."""

SEED_DOMAIN_MAPPINGS: list[tuple[str, str]] = [
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

- [ ] **Step 2: Write M015 migration**

Create `src/superlocalmemory/storage/migrations/M015_add_domain_tags.py`:

```python
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""M015 — domain_mapping table and domain_tags columns (memory.db, deferred).

Creates the domain_mapping table for entity→domain lookups.
Adds domain_tags TEXT column to 4 core tables (atomic_facts,
canonical_entities, graph_edges, temporal_events).
Seeds ~50 built-in entity→domain mappings via post_ddl_hook.
"""

from __future__ import annotations

import sqlite3

NAME = "M015_add_domain_tags"
DB_TARGET = "memory"

TABLES = [
    "atomic_facts",
    "canonical_entities",
    "graph_edges",
    "temporal_events",
]

DDL = ";".join(
    ["CREATE TABLE IF NOT EXISTS domain_mapping (entity_name TEXT NOT NULL, domain TEXT NOT NULL, PRIMARY KEY (entity_name, domain))"]
    + [f"ALTER TABLE {t} ADD COLUMN domain_tags TEXT" for t in TABLES]
)


def verify(conn: sqlite3.Connection) -> bool:
    """Check if migration already applied by inspecting domain_mapping table."""
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='domain_mapping'"
        ).fetchall()
        if not rows:
            return False
        cols = {r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)").fetchall()}
        return "domain_tags" in cols
    except sqlite3.Error:
        return False


def post_ddl_hook(conn: sqlite3.Connection) -> None:
    """Seed domain_mapping with built-in entity→domain entries."""
    from superlocalmemory.storage.seed_domain_mapping import SEED_DOMAIN_MAPPINGS

    conn.executemany(
        "INSERT OR IGNORE INTO domain_mapping (entity_name, domain) VALUES (?, ?)",
        SEED_DOMAIN_MAPPINGS,
    )
    conn.commit()
```

- [ ] **Step 3: Register M015 in migration_runner.py**

In `src/superlocalmemory/storage/migration_runner.py`:

Add import at line ~53 (after M014 import):

```python
    M015_add_domain_tags as _M015,
```

Add to `_MODULES` dict at line ~72 (after M014 entry):

```python
    _M015.NAME: _M015,
```

Add to `DEFERRED_MIGRATIONS` list at line ~135 (after M014 entry):

```python
    # M015 adds domain_mapping table and domain_tags columns. Deferred for the
    # same engine-init-bootstrap reason as M014.
    Migration(name=_M015.NAME, db_target="memory", ddl=_M015.DDL),
```

- [ ] **Step 4: Run migration tests to verify registration**

Run: `pytest tests/test_storage/test_migration_runner.py -v -k "idempotent or deferred" --tb=short`
Expected: PASS (no new failures from registration)

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/storage/seed_domain_mapping.py \
        src/superlocalmemory/storage/migrations/M015_add_domain_tags.py \
        src/superlocalmemory/storage/migration_runner.py
git commit -m "feat(phase2): M015 migration — domain_mapping + domain_tags + seed data"
```

---

### Task 4: DatabaseManager — resolve_domain_tags + _scope_where extension + store_fact update

**Files:**
- Modify: `src/superlocalmemory/storage/database.py`

- [ ] **Step 1: Write failing test for resolve_domain_tags and domain-aware _scope_where**

Create `tests/test_domain_tags_db.py`:

```python
"""DatabaseManager domain tag tests — resolve_domain_tags + _scope_where."""
import sqlite3
import pytest
from superlocalmemory.storage.database import DatabaseManager


@pytest.fixture
def db_with_mappings(in_memory_db):
    """In-memory DB with seed domain_mapping rows."""
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')"
    )
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('TypeScript', 'frontend')"
    )
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('PostgreSQL', 'backend')"
    )
    in_memory_db.commit()
    return in_memory_db


def test_resolve_domain_tags_empty_input(in_memory_db):
    """Empty entity list returns empty domain list."""
    assert in_memory_db.resolve_domain_tags([]) == []


def test_resolve_domain_tags_single_match(db_with_mappings):
    """Single matching entity returns its domain."""
    result = db_with_mappings.resolve_domain_tags(["React"])
    assert result == ["frontend"]


def test_resolve_domain_tags_multiple_same_domain(db_with_mappings):
    """Multiple entities in same domain return deduplicated domains."""
    result = db_with_mappings.resolve_domain_tags(["React", "TypeScript"])
    assert result == ["frontend"]


def test_resolve_domain_tags_cross_domain(db_with_mappings):
    """Entities from different domains return all matched domains."""
    result = db_with_mappings.resolve_domain_tags(["React", "PostgreSQL"])
    assert set(result) == {"frontend", "backend"}


def test_resolve_domain_tags_no_match(db_with_mappings):
    """Entity not in mapping returns empty list."""
    result = db_with_mappings.resolve_domain_tags(["Unknown"])
    assert result == []


def test_scope_where_with_skill_tags():
    """_scope_where with skill_tags produces domain overlap condition."""
    clause, params = DatabaseManager._scope_where(
        "alice", "personal", False, True, "", skill_tags=["backend", "devops"],
    )
    assert "domain_tags IS NOT NULL" in clause
    assert "json_each" in clause
    assert "backend" in params
    assert "devops" in params


def test_scope_where_without_skill_tags():
    """_scope_where without skill_tags has no domain condition (baseline)."""
    clause, params = DatabaseManager._scope_where(
        "alice", "personal", False, True, "",
    )
    assert "domain_tags" not in clause


def test_domain_recall_visibility(in_memory_db):
    """Agent with matching skill_tags sees domain-tagged facts."""
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f1', 'm1', 'a', 'react tip', 'personal', '[\"frontend\"]')"
    )
    in_memory_db.commit()

    # Agent B with frontend skill sees the fact via domain overlap
    clause, params = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["frontend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause}", params,
    )
    contents = [r["content"] for r in rows]
    assert "react tip" in contents

    # Agent B with only backend skill does NOT see the frontend-tagged fact
    clause2, params2 = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["backend"],
    )
    rows2 = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause2}", params2,
    )
    contents2 = [r["content"] for r in rows2]
    assert "react tip" not in contents2


def test_domain_and_shared_with_coexist(in_memory_db):
    """Both shared_with and domain matching return results."""
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m2', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, shared_with) "
        "VALUES ('f1', 'm1', 'a', 'shared fact', 'personal', '[\"b\"]')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f2', 'm2', 'a', 'domain fact', 'personal', '[\"backend\"]')"
    )
    in_memory_db.commit()

    clause, params = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["backend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause}", params,
    )
    contents = [r["content"] for r in rows]
    assert "shared fact" in contents
    assert "domain fact" in contents
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_domain_tags_db.py -v --tb=short`
Expected: FAIL — `resolve_domain_tags` method doesn't exist, `_scope_where` doesn't accept `skill_tags`

- [ ] **Step 3: Implement resolve_domain_tags method**

In `src/superlocalmemory/storage/database.py`, add after `_scope_where` (after line ~112):

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

- [ ] **Step 4: Extend _scope_where with skill_tags parameter**

In `src/superlocalmemory/storage/database.py`, update `_scope_where` signature (line ~81):

```python
    @staticmethod
    def _scope_where(
        profile_id: str,
        scope: str = "personal",
        include_global: bool = True,
        include_shared: bool = True,
        table_alias: str = "",
        skill_tags: list[str] | None = None,
    ) -> tuple[str, list]:
```

Add domain-overlap condition after the `include_shared` block (after line ~105):

```python
        if include_shared:
            conditions.append(f"? IN (SELECT value FROM json_each({prefix}shared_with))")
            params.append(profile_id)

        # Phase 2: domain tag overlap matching
        if include_shared and skill_tags:
            domain_placeholders = ",".join("?" * len(skill_tags))
            conditions.append(
                f"({prefix}domain_tags IS NOT NULL AND EXISTS ("
                f"SELECT 1 FROM json_each({prefix}domain_tags) "
                f"WHERE value IN ({domain_placeholders})))"
            )
            params.extend(skill_tags)
```

- [ ] **Step 5: Update store_fact to include domain_tags column**

In `src/superlocalmemory/storage/database.py`, update `store_fact` INSERT statement (line ~253). The current INSERT has 28 columns/values. Add `domain_tags` as the 29th column after `shared_with`:

```python
    def store_fact(self, fact: AtomicFact) -> str:
        """Persist an atomic fact. Returns fact_id."""
        self.execute(
            """INSERT OR REPLACE INTO atomic_facts
               (fact_id, memory_id, profile_id, content, fact_type,
                entities_json, canonical_entities_json,
                observation_date, referenced_date, interval_start, interval_end,
                confidence, importance, evidence_count, access_count,
                source_turn_ids_json, session_id,
                embedding, fisher_mean, fisher_variance,
                lifecycle, langevin_position,
                emotional_valence, emotional_arousal, signal_type, created_at,
                scope, shared_with, domain_tags)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fact.fact_id, fact.memory_id, fact.profile_id, fact.content,
                fact.fact_type.value, json.dumps(fact.entities),
                json.dumps(fact.canonical_entities), fact.observation_date,
                fact.referenced_date, fact.interval_start, fact.interval_end,
                fact.confidence, fact.importance, fact.evidence_count,
                fact.access_count, json.dumps(fact.source_turn_ids),
                fact.session_id, _jd(fact.embedding), _jd(fact.fisher_mean),
                _jd(fact.fisher_variance), fact.lifecycle.value,
                _jd(fact.langevin_position), fact.emotional_valence,
                fact.emotional_arousal, fact.signal_type.value,
                fact.created_at, fact.scope, _jd(fact.shared_with),
                _jd(fact.domain_tags),
            ),
        )
        return fact.fact_id
```

- [ ] **Step 6: Update _row_to_fact to parse domain_tags**

In `src/superlocalmemory/storage/database.py`, update `_row_to_fact` (line ~298):

Add after `shared_with` parsing:
```python
            domain_tags=json.loads(d["domain_tags"]) if d.get("domain_tags") else None,
```

- [ ] **Step 7: Update DB methods to accept and forward skill_tags**

These DB methods call `_scope_where` with `include_shared=True` and must gain a `skill_tags` parameter to forward it. Add `skill_tags: list[str] | None = None` as a keyword parameter to each, and pass it to the `_scope_where` call:

- `get_all_facts` (line ~336) — add `skill_tags=skill_tags` to `_scope_where()` call
- `get_facts_by_entity` (line ~357) — same
- `get_facts_by_type` (line ~381) — same
- `get_edges_for_node` (line ~589) — same
- `get_entity_by_name` (line ~488) — same
- `get_temporal_events` (line ~642) — same
- `search_facts_fts` (line ~689) — same (uses `table_alias="f"`)
- Any other method that calls `_scope_where(..., include_shared=True)`

Pattern for each:
```python
def get_all_facts(
    self,
    profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    skill_tags: list[str] | None = None,
) -> list[AtomicFact]:
    where_clause, params = self._scope_where(
        profile_id, scope, include_global, include_shared, skill_tags=skill_tags,
    )
```

Find all callers with: `grep -n "include_shared" src/superlocalmemory/storage/database.py | grep "def "`

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_domain_tags_db.py -v --tb=short`
Expected: All 10 tests pass

- [ ] **Step 9: Chunk 1 regression check**

Run: `pytest tests/test_domain_tags_schema.py tests/test_domain_tags_db.py tests/test_scope_integration.py tests/test_storage/test_migration_runner.py -q --tb=short`
Expected: All pass (no regressions from Chunk 1)

- [ ] **Step 10: Commit**

```bash
git add tests/test_domain_tags_db.py src/superlocalmemory/storage/database.py
git commit -m "feat(phase2): resolve_domain_tags + domain-aware _scope_where + store_fact"
```

> **Design note:** Per spec, only `atomic_facts` gets `domain_tags` populated at store time. The other 3 tables (`canonical_entities`, `graph_edges`, `temporal_events`) have the column via migration but it stays NULL. The `_scope_where` domain condition checks `domain_tags IS NOT NULL` first, so NULL values on those tables are harmless — they won't produce false positives in domain matching.

---

## Chunk 2: Pipeline Integration — Store, Recall, Config

### Task 5: StorePipeline — domain tag resolution in enrich_fact

**Files:**
- Modify: `src/superlocalmemory/core/store_pipeline.py`

- [ ] **Step 1: Write failing test for domain tag resolution in store pipeline**

Add to `tests/test_domain_tags_db.py`:

```python
def test_enrich_fact_resolves_domain_tags(in_memory_db):
    """enrich_fact resolves domain_tags from entity names."""
    from unittest.mock import MagicMock
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord
    from superlocalmemory.core.store_pipeline import enrich_fact

    # Seed a mapping
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')"
    )
    in_memory_db.commit()

    entity_resolver = MagicMock()
    entity_resolver.resolve.return_value = {"React": "react_01"}

    fact = AtomicFact(
        content="React uses JSX",
        entities=["React"],
        fact_type=MagicMock(value="semantic"),
    )
    record = MemoryRecord(memory_id="m1", profile_id="test")
    embedder = MagicMock()
    embedder.embed.return_value = None

    enriched = enrich_fact(
        fact, record, "test",
        embedder=embedder,
        entity_resolver=entity_resolver,
        temporal_parser=None,
        db=in_memory_db,
    )
    assert enriched.domain_tags == ["frontend"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_domain_tags_db.py::test_enrich_fact_resolves_domain_tags -v --tb=short`
Expected: FAIL — `enrich_fact` doesn't accept `db` parameter

- [ ] **Step 3: Add db parameter to enrich_fact and resolve domain tags**

In `src/superlocalmemory/core/store_pipeline.py`, update `enrich_fact` signature (line ~57):

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
) -> AtomicFact:
```

After entity resolution (after line ~76, after `canonical = entity_resolver.resolve(...)`), add:

```python
    # Phase 2: resolve domain tags from canonical entity names
    domain_tags = None
    if db and canonical:
        domain_tags = db.resolve_domain_tags(list(canonical.keys()))
```

Add `domain_tags=domain_tags,` to the returned `AtomicFact(...)` constructor call.

- [ ] **Step 4: Pass db from run_store to enrich_fact**

In `src/superlocalmemory/core/store_pipeline.py`, find where `enrich_fact` is called in `run_store` (or `_store_facts`) and add `db=db` to the keyword arguments. The `run_store` function already receives `db` as a keyword argument (it's used for `db.store_fact()`).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_domain_tags_db.py::test_enrich_fact_resolves_domain_tags -v --tb=short`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_domain_tags_db.py src/superlocalmemory/core/store_pipeline.py
git commit -m "feat(phase2): enrich_fact resolves domain tags from entity names"
```

---

### Task 6: SLMConfig + skill_tags propagation through recall chain

**Files:**
- Modify: `src/superlocalmemory/core/config.py`
- Modify: `src/superlocalmemory/retrieval/engine.py`
- Modify: `src/superlocalmemory/core/engine_wiring.py` (if needed)

- [ ] **Step 1: Write failing test for skill_tags in SLMConfig**

Add to `tests/test_domain_tags_db.py`:

```python
def test_slm_config_skill_tags():
    """SLMConfig has skill_tags field."""
    from superlocalmemory.core.config import SLMConfig
    config = SLMConfig(skill_tags=["backend", "devops"])
    assert config.skill_tags == ["backend", "devops"]


def test_slm_config_skill_tags_default():
    """SLMConfig skill_tags defaults to empty list."""
    from superlocalmemory.core.config import SLMConfig
    config = SLMConfig()
    assert config.skill_tags == []


def test_profile_skill_tags():
    """Profile.skill_tags reads from config dict."""
    from superlocalmemory.storage.models import Profile
    p = Profile(profile_id="x", name="X", config={"skill_tags": ["frontend"]})
    assert p.skill_tags == ["frontend"]


def test_profile_skill_tags_default():
    """Profile.skill_tags defaults to empty list."""
    from superlocalmemory.storage.models import Profile
    p = Profile(profile_id="x", name="X")
    assert p.skill_tags == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_domain_tags_db.py::test_slm_config_skill_tags tests/test_domain_tags_db.py::test_slm_config_skill_tags_default -v --tb=short`
Expected: FAIL — SLMConfig doesn't have `skill_tags` field

- [ ] **Step 3: Add skill_tags to SLMConfig**

In `src/superlocalmemory/core/config.py`, add to `SLMConfig` dataclass (after line ~586, after `active_profile`):

```python
    skill_tags: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Add skill_tags to RetrievalEngine constructor**

In `src/superlocalmemory/retrieval/engine.py`, update constructor (line ~65):

```python
    def __init__(
        self,
        db: DatabaseManager,
        config: RetrievalConfig,
        channels: dict[str, Any],
        embedder: EmbeddingProvider | None = None,
        reranker: CrossEncoderProtocol | None = None,
        strategy: QueryStrategyClassifier | None = None,
        base_weights: ChannelWeights | None = None,
        profile_channel: Any | None = None,
        bridge_discovery: Any | None = None,
        trust_scorer: TrustScorer | None = None,
        skill_tags: list[str] | None = None,
    ) -> None:
```

Add at end of constructor body:
```python
        self._skill_tags = skill_tags or []
```

- [ ] **Step 5: Propagate skill_tags from engine_wiring**

In `src/superlocalmemory/core/engine_wiring.py`, find where `RetrievalEngine(...)` is constructed and pass `skill_tags=config.skill_tags`. Search for the exact construction site:

```bash
grep -n "RetrievalEngine(" src/superlocalmemory/core/engine_wiring.py
```

Add `skill_tags=config.skill_tags,` to the RetrievalEngine constructor call, where `config` is the SLMConfig instance.

- [ ] **Step 5b: Load skill_tags from active profile during engine initialization**

`MemoryEngine` doesn't have `_profile_manager` — use `ProfileManager` directly. Find where `self._config` is set during `initialize()` and add:

```python
# Phase 2: load skill_tags from active profile config
try:
    from superlocalmemory.core.profiles import ProfileManager
    pm = ProfileManager(self._config.base_dir)
    profile = pm.get_active_profile()
    if profile and profile.skill_tags:
        self._config.skill_tags = profile.skill_tags
except Exception:
    pass  # skill_tags defaults to []
```

Check the actual engine init flow with:

```bash
grep -n "def initialize\|self._config\|base_dir" src/superlocalmemory/core/engine.py | head -20
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_domain_tags_db.py -k "slm_config or profile_skill" -v --tb=short`
Expected: All 4 tests pass

- [ ] **Step 7: Commit**

```bash
git add tests/test_domain_tags_db.py src/superlocalmemory/core/config.py \
        src/superlocalmemory/retrieval/engine.py \
        src/superlocalmemory/core/engine_wiring.py
git commit -m "feat(phase2): skill_tags in SLMConfig + RetrievalEngine"
```

---

### Task 7: RetrievalEngine — pass skill_tags to _run_channels + channel search methods

**Files:**
- Modify: `src/superlocalmemory/retrieval/engine.py`
- Modify: `src/superlocalmemory/retrieval/semantic_channel.py`
- Modify: `src/superlocalmemory/retrieval/temporal_channel.py`
- Modify: other channels as needed

- [ ] **Step 1: Write failing test for skill_tags propagation to DB calls**

Add to `tests/test_domain_tags_db.py`:

```python
def test_retrieval_engine_stores_skill_tags():
    """RetrievalEngine constructor stores skill_tags."""
    from unittest.mock import MagicMock
    from superlocalmemory.retrieval.engine import RetrievalEngine

    engine = RetrievalEngine(
        db=MagicMock(), config=MagicMock(), channels={},
        skill_tags=["backend"],
    )
    assert engine._skill_tags == ["backend"]
```

- [ ] **Step 2: Verify the test passes (constructor already updated in Task 6)**

Run: `pytest tests/test_domain_tags_db.py::test_retrieval_engine_stores_skill_tags -v --tb=short`
Expected: PASS

- [ ] **Step 3: Pass skill_tags to _run_channels and channel search calls**

In `src/superlocalmemory/retrieval/engine.py`, update `_run_channels` (line ~524) to accept and forward `skill_tags`:

```python
def _run_channels(
    self,
    query: str,
    profile_id: str,
    strat: QueryStrategy,
    *,
    scope: str = "personal",
    skill_tags: list[str] | None = None,
) -> dict[str, list[tuple[str, float]]]:
```

In `recall()` (line ~183-188), pass `skill_tags=self._skill_tags` only for the `scope="shared"` call:

```python
# Shared scope (includes domain tag matching)
if include_shared:
    shared_ch = self._run_channels(
        query, profile_id, strat, scope="shared", skill_tags=self._skill_tags,
    )
```

In `_run_channels`, forward `skill_tags` to each channel's `search()` call:

```python
r = self._semantic.search(q_emb, profile_id, self._config.semantic_top_k, scope=scope, skill_tags=skill_tags)
```

Note: Per spec, BM25 channel does NOT receive `skill_tags` — it ignores scope filtering entirely.

- [ ] **Step 4: Update channel search signatures to accept and forward skill_tags**

For each channel that calls DB methods with `include_shared=True`, add `skill_tags: list[str] | None = None` keyword parameter to `search()` and pass it to the DB method call.

Check each channel with:
```bash
grep -n "include_shared" src/superlocalmemory/retrieval/semantic_channel.py src/superlocalmemory/retrieval/entity_channel.py src/superlocalmemory/retrieval/temporal_channel.py src/superlocalmemory/retrieval/hopfield_channel.py src/superlocalmemory/retrieval/spreading_activation.py
```

For each channel that has `include_shared=True` calls, update pattern:
```python
def search(self, query, profile_id, top_k=50, *, scope="personal", skill_tags=None):
    facts = self._db.get_all_facts(profile_id, include_shared=True, skill_tags=skill_tags)
```

The DB methods were updated in Task 4 Step 7 to accept `skill_tags`.

- [ ] **Step 5: Chunk 2 regression check**

Run: `pytest tests/test_domain_tags_db.py tests/test_retrieval/ -q --tb=short`
Expected: All pass (no regressions from Chunk 2)

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/retrieval/engine.py src/superlocalmemory/retrieval/semantic_channel.py src/superlocalmemory/retrieval/entity_channel.py src/superlocalmemory/retrieval/temporal_channel.py
git commit -m "feat(phase2): skill_tags propagation through retrieval channels"
```

---

## Chunk 3: MCP Tool, End-to-End Tests, Verification

### Task 8: MCP tool — add_domain_mapping

**Files:**
- Modify: `src/superlocalmemory/mcp/tools_core.py`

- [ ] **Step 1: Write failing test for add_domain_mapping MCP tool**

Add to `tests/test_domain_tags_db.py`:

```python
def test_add_domain_mapping_tool(in_memory_db, monkeypatch):
    """add_domain_mapping MCP tool inserts mapping into domain_mapping table."""
    from unittest.mock import MagicMock
    from superlocalmemory.mcp.tools_core import register_core_tools

    server = MagicMock()
    tools = {}

    def capture_tool(annotations=None):
        def decorator(fn):
            tools[fn.__name__] = fn
            return fn
        return decorator

    server.tool = capture_tool

    mock_engine = MagicMock()
    mock_engine.profile_id = "test"
    mock_engine._db = in_memory_db
    register_core_tools(server, lambda: mock_engine)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        tools["add_domain_mapping"](entity_name="SolidJS", domain="frontend")
    )

    assert result["success"] is True
    rows = in_memory_db.execute(
        "SELECT domain FROM domain_mapping WHERE entity_name = 'SolidJS'"
    )
    domains = [r["domain"] for r in rows]
    assert "frontend" in domains


def test_add_domain_mapping_duplicate_is_noop(in_memory_db, monkeypatch):
    """Adding a duplicate mapping is idempotent (INSERT OR IGNORE)."""
    from unittest.mock import MagicMock
    from superlocalmemory.mcp.tools_core import register_core_tools

    server = MagicMock()
    tools = {}
    server.tool = lambda annotations=None: (lambda fn: (tools.update({fn.__name__: fn}), fn)[1])

    mock_engine = MagicMock()
    mock_engine.profile_id = "test"
    mock_engine._db = in_memory_db
    register_core_tools(server, lambda: mock_engine)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        tools["add_domain_mapping"](entity_name="SolidJS", domain="frontend")
    )
    result = asyncio.get_event_loop().run_until_complete(
        tools["add_domain_mapping"](entity_name="SolidJS", domain="frontend")
    )

    assert result["success"] is True
    rows = in_memory_db.execute(
        "SELECT COUNT(*) as c FROM domain_mapping WHERE entity_name = 'SolidJS'"
    )
    assert rows[0]["c"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_domain_tags_db.py -k "add_domain_mapping" -v --tb=short`
Expected: FAIL — `add_domain_mapping` tool not registered

- [ ] **Step 3: Implement add_domain_mapping MCP tool**

In `src/superlocalmemory/mcp/tools_core.py`, add after the existing `recall` tool:

```python
@_tool(annotations={"readOnlyHint": False})
async def add_domain_mapping(entity_name: str, domain: str) -> dict:
    """Add an entity-to-domain mapping for skill-based memory sharing.

    Example: add_domain_mapping("Kubernetes", "devops")
    """
    try:
        engine = _get_engine()
        engine._db.execute(
            "INSERT OR IGNORE INTO domain_mapping (entity_name, domain) VALUES (?, ?)",
            (entity_name, domain),
        )
        return {"success": True, "mapping": {"entity_name": entity_name, "domain": domain}}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
```

Note: Use the same `@_tool` decorator pattern and `_get_engine()` helper as other tools in the file. Adjust the decorator to match the file's actual pattern (it may use `server.tool()` directly).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_domain_tags_db.py -k "add_domain_mapping" -v --tb=short`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_domain_tags_db.py src/superlocalmemory/mcp/tools_core.py
git commit -m "feat(phase2): add_domain_mapping MCP tool"
```

---

### Task 9: End-to-end integration tests

**Files:**
- Create: `tests/test_domain_tags_integration.py`

- [ ] **Step 1: Write end-to-end integration tests**

Create `tests/test_domain_tags_integration.py`:

```python
"""Phase 2 end-to-end integration tests — domain tag store/recall flow."""
from __future__ import annotations

import pytest


def test_store_with_entity_auto_tags(in_memory_db):
    """Storing content with a known entity auto-tags the fact with domain."""
    # Seed mapping
    in_memory_db.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')"
    )
    in_memory_db.commit()

    # Simulate what store pipeline does
    from superlocalmemory.storage.database import DatabaseManager
    domains = in_memory_db.resolve_domain_tags(["React"])
    assert domains == ["frontend"]


def test_store_no_matching_entity_no_tags(in_memory_db):
    """Content with unknown entity produces no domain tags."""
    domains = in_memory_db.resolve_domain_tags(["ObscureFramework"])
    assert domains == []


def test_cross_agent_domain_sharing(in_memory_db):
    """Agent B with matching skill sees domain-tagged fact from Agent A."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f1', 'm1', 'a', 'docker-compose tip', 'personal', "
        "'[\"devops\"]')"
    )
    in_memory_db.commit()

    # Agent B with devops skill sees it
    where_b, params_b = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["devops"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where_b}", params_b,
    )
    assert any(r["content"] == "docker-compose tip" for r in rows)

    # Agent B with only frontend skill does NOT see it
    where_b2, params_b2 = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["frontend"],
    )
    rows2 = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where_b2}", params_b2,
    )
    assert not any(r["content"] == "docker-compose tip" for r in rows2)


def test_seed_data_loads_via_post_ddl_hook(in_memory_db):
    """M015 post_ddl_hook seeds domain_mapping correctly."""
    from superlocalmemory.storage.migrations.M015_add_domain_tags import post_ddl_hook

    # Simulate migration: seed data into fresh schema
    post_ddl_hook(in_memory_db._conn if hasattr(in_memory_db, '_conn') else in_memory_db)

    rows = in_memory_db.execute("SELECT COUNT(*) as c FROM domain_mapping")
    count = rows[0]["c"]
    assert count >= 35, f"Expected ~50 seed mappings, got {count}"


def test_null_domain_tags_invisible_to_domain_matching(in_memory_db):
    """Facts with domain_tags=NULL are NOT matched by domain overlap."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f1', 'm1', 'a', 'untagged fact', 'personal', NULL)"
    )
    in_memory_db.commit()

    where, params = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["frontend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where}", params,
    )
    assert not any(r["content"] == "untagged fact" for r in rows)
```

- [ ] **Step 2: Run all Phase 2 tests**

Run: `pytest tests/test_domain_tags_schema.py tests/test_domain_tags_db.py tests/test_domain_tags_integration.py tests/test_scope_integration.py -v --tb=short`
Expected: All pass

- [ ] **Step 3: Chunk 3 regression check (subsystems only, NOT full suite)**

Run: `pytest tests/test_domain_tags_schema.py tests/test_domain_tags_db.py tests/test_domain_tags_integration.py tests/test_scope_integration.py tests/test_storage/test_migration_runner.py tests/test_retrieval/ -q --tb=short`
Expected: No new failures

- [ ] **Step 4: Commit**

```bash
git add tests/test_domain_tags_integration.py
git commit -m "test(phase2): end-to-end integration tests for domain tags"
```

---

### Task 10: Verify migration on real database

**Files:** None (verification only)

- [ ] **Step 1: Run slm doctor to verify migration applies cleanly**

```bash
slm doctor
```

Expected: M015 migration applies, no errors, `domain_mapping` table created

- [ ] **Step 2: Verify seed data in real DB**

```bash
sqlite3 ~/.superlocalmemory/memory.db "SELECT COUNT(*) FROM domain_mapping;"
```

Expected: ~50 rows

- [ ] **Step 3: Verify domain_tags column exists**

```bash
sqlite3 ~/.superlocalmemory/memory.db "PRAGMA table_info(atomic_facts);" | grep domain_tags
```

Expected: One row showing domain_tags column

- [ ] **Step 4: Test add_domain_mapping via MCP**

```bash
sqlite3 ~/.superlocalmemory/memory.db "INSERT OR IGNORE INTO domain_mapping (entity_name, domain) VALUES ('TestEntity', 'test_domain'); SELECT * FROM domain_mapping WHERE entity_name='TestEntity';"
```

Clean up test data:
```bash
sqlite3 ~/.superlocalmemory/memory.db "DELETE FROM domain_mapping WHERE entity_name='TestEntity';"
```

---

### Task 11: Final cleanup and commit

**Files:**
- All modified files

- [ ] **Step 1: Run ruff on all modified files**

```bash
ruff check src/superlocalmemory/storage/database.py src/superlocalmemory/storage/models.py src/superlocalmemory/storage/schema.py src/superlocalmemory/storage/seed_domain_mapping.py src/superlocalmemory/storage/migrations/M015_add_domain_tags.py src/superlocalmemory/storage/migration_runner.py src/superlocalmemory/core/store_pipeline.py src/superlocalmemory/core/config.py src/superlocalmemory/retrieval/engine.py src/superlocalmemory/mcp/tools_core.py
```

Expected: No errors

- [ ] **Step 2: Run ruff format on modified files**

```bash
ruff format src/superlocalmemory/storage/database.py src/superlocalmemory/storage/models.py src/superlocalmemory/storage/schema.py src/superlocalmemory/storage/seed_domain_mapping.py src/superlocalmemory/storage/migrations/M015_add_domain_tags.py src/superlocalmemory/storage/migration_runner.py src/superlocalmemory/core/store_pipeline.py src/superlocalmemory/core/config.py src/superlocalmemory/retrieval/engine.py src/superlocalmemory/mcp/tools_core.py
```

- [ ] **Step 3: Run full test suite one final time**

```bash
pytest tests/ -q --tb=short -x --ignore=tests/test_code_graph --ignore=tests/test_integration -m "not slow and not ollama and not benchmark" 2>&1 | tail -10
```

Expected: Same pass rate as before Phase 2 changes

- [ ] **Step 4: Final commit if any formatting fixes needed**

```bash
git add -u && git commit -m "style(phase2): ruff formatting cleanup"
```
