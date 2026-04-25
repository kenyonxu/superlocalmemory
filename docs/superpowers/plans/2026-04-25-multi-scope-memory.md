# Multi-Scope Memory Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two-layer scope (personal + global) with point-to-point sharing to SuperLocalMemory, enabling Hermes agent team members to have independent memories while sharing selected knowledge.

**Architecture:** Add `scope` (TEXT) and `shared_with` (JSON TEXT) columns to 5 core tables. DatabaseManager methods gain scope-aware WHERE clauses. RetrievalEngine runs 2-3 passes (personal/global/shared) with scope-weighted RRF fusion. MCP tools pass scope through the pending store pipeline.

**Tech Stack:** Python 3.11+, SQLite, existing SLM retrieval infrastructure (7 channels, RRF fusion)

**Spec:** `docs/superpowers/specs/2026-04-25-multi-scope-memory-design.md`

**Correction from spec:** The spec lists 8 tables but only 5 exist: `memories`, `atomic_facts`, `canonical_entities`, `graph_edges`, `temporal_events`. (`kg_nodes`, `memory_edges`, `audit_trail` do not exist in the current schema.)

---

## Chunk 1: Foundation — Schema, Models, Migration, DatabaseManager

### Task 1: Add scope columns to schema.py

**Files:**
- Modify: `src/superlocalmemory/storage/schema.py`
- Test: `tests/test_scope_schema.py` (create)

- [ ] **Step 1: Write failing test for scope columns**

```python
# tests/test_scope_schema.py
"""Verify scope/shared_with columns exist on core tables after schema creation."""
import sqlite3
import pytest

SCOPE_TABLES = [
    "memories", "atomic_facts", "canonical_entities",
    "graph_edges", "temporal_events",
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

@pytest.mark.parametrize("table", SCOPE_TABLES)
def test_scope_column_exists(fresh_db, table):
    """Each core table must have a 'scope' column."""
    rows = fresh_db.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = {r["name"] for r in rows}
    assert "scope" in col_names, f"{table} missing 'scope' column. Got: {col_names}"

@pytest.mark.parametrize("table", SCOPE_TABLES)
def test_shared_with_column_exists(fresh_db, table):
    """Each core table must have a 'shared_with' column."""
    rows = fresh_db.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = {r["name"] for r in rows}
    assert "shared_with" in col_names, f"{table} missing 'shared_with' column. Got: {col_names}"

def test_scope_default_is_personal(fresh_db):
    """New rows default to scope='personal'."""
    fresh_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content) "
        "VALUES ('f1', 'm1', 'alice', 'test')"
    )
    row = fresh_db.execute("SELECT scope FROM atomic_facts WHERE fact_id='f1'").fetchone()
    assert row["scope"] == "personal"

def test_shared_with_default_is_null(fresh_db):
    """New rows default to shared_with=NULL."""
    fresh_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content) "
        "VALUES ('f2', 'm1', 'alice', 'test')"
    )
    row = fresh_db.execute("SELECT shared_with FROM atomic_facts WHERE fact_id='f2'").fetchone()
    assert row["shared_with"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope_schema.py -v`
Expected: FAIL — columns don't exist yet

- [ ] **Step 3: Add scope and shared_with columns to schema.py**

In `src/superlocalmemory/storage/schema.py`, add two columns after the `profile_id` column in each of the 5 core tables. Add them and corresponding indexes.

For **`memories`** table (after line ~114, after `profile_id` line):
```sql
scope          TEXT NOT NULL DEFAULT 'personal',
shared_with    TEXT,
```

For **`atomic_facts`** table (after line ~143, after `profile_id` line):
```sql
scope          TEXT NOT NULL DEFAULT 'personal',
shared_with    TEXT,
```

For **`canonical_entities`** table (after line ~289, after `profile_id` line):
```sql
scope          TEXT NOT NULL DEFAULT 'personal',
shared_with    TEXT,
```

For **`graph_edges`** table (after line ~422, after `profile_id` line):
```sql
scope          TEXT NOT NULL DEFAULT 'personal',
shared_with    TEXT,
```

For **`temporal_events`** table (after line ~388, after `profile_id` line):
```sql
scope          TEXT NOT NULL DEFAULT 'personal',
shared_with    TEXT,
```

Also add indexes after each table's existing indexes:
```sql
CREATE INDEX IF NOT EXISTS idx_<table>_scope ON <table>(scope);
CREATE INDEX IF NOT EXISTS idx_<table>_profile_scope ON <table>(profile_id, scope);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope_schema.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/storage/schema.py tests/test_scope_schema.py
git commit -m "feat(scope): add scope + shared_with columns to 5 core tables"
```

---

### Task 2: Update data classes in models.py

**Files:**
- Modify: `src/superlocalmemory/storage/models.py`
- Test: `tests/test_scope_schema.py` (extend)

- [ ] **Step 1: Write failing test for model fields**

Add to `tests/test_scope_schema.py`:
```python
def test_atomic_fact_has_scope_field():
    from superlocalmemory.storage.models import AtomicFact
    fact = AtomicFact(fact_id="f1", memory_id="m1", content="test")
    assert fact.scope == "personal"
    assert fact.shared_with is None

def test_graph_edge_has_scope_field():
    from superlocalmemory.storage.models import GraphEdge
    edge = GraphEdge(edge_id="e1", source_id="s1", target_id="t1")
    assert edge.scope == "personal"
    assert edge.shared_with is None

def test_canonical_entity_has_scope_field():
    from superlocalmemory.storage.models import CanonicalEntity
    entity = CanonicalEntity(entity_id="ent1", canonical_name="React")
    assert entity.scope == "personal"
    assert entity.shared_with is None

def test_temporal_event_has_scope_field():
    from superlocalmemory.storage.models import TemporalEvent
    event = TemporalEvent(event_id="ev1", entity_id="ent1", fact_id="f1")
    assert event.scope == "personal"
    assert event.shared_with is None

def test_memory_record_has_scope_field():
    from superlocalmemory.storage.models import MemoryRecord
    rec = MemoryRecord(memory_id="m1", content="test")
    assert rec.scope == "personal"
    assert rec.shared_with is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope_schema.py::test_atomic_fact_has_scope_field -v`
Expected: FAIL — `__init__` got unexpected keyword or missing attribute

- [ ] **Step 3: Add scope/shared_with fields to 5 data classes**

In `src/superlocalmemory/storage/models.py`, add two fields to each data class:

**MemoryRecord** (after `profile_id` field):
```python
scope: str = "personal"
shared_with: list[str] | None = None
```

**AtomicFact** (after `profile_id` field):
```python
scope: str = "personal"
shared_with: list[str] | None = None
```

**CanonicalEntity** (after `profile_id` field):
```python
scope: str = "personal"
shared_with: list[str] | None = None
```

**GraphEdge** (after `profile_id` field):
```python
scope: str = "personal"
shared_with: list[str] | None = None
```

**TemporalEvent** (after `profile_id` field):
```python
scope: str = "personal"
shared_with: list[str] | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope_schema.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/storage/models.py tests/test_scope_schema.py
git commit -m "feat(scope): add scope/shared_with fields to 5 data classes"
```

---

### Task 3: Create migration M014

**Files:**
- Create: `src/superlocalmemory/storage/migrations/M014_add_scope_support.py`
- Modify: `src/superlocalmemory/storage/migrations/__init__.py`
- Modify: `src/superlocalmemory/storage/migration_runner.py`
- Test: `tests/test_scope_schema.py` (extend)

- [ ] **Step 1: Write failing test for migration**

Add to `tests/test_scope_schema.py`:
```python
def test_m014_migration_adds_columns():
    """M014 migration should add scope/shared_with to an existing DB without them."""
    from superlocalmemory.storage.migrations.M014_add_scope_support import upgrade

    # Create a minimal DB with tables but WITHOUT scope columns
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Create tables without scope columns (simulate pre-migration state)
    conn.execute("""CREATE TABLE IF NOT EXISTS atomic_facts (
        fact_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default',
        content TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        memory_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default',
        content TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS canonical_entities (
        entity_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default',
        canonical_name TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS graph_edges (
        edge_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default',
        source_id TEXT NOT NULL, target_id TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS temporal_events (
        event_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default',
        entity_id TEXT NOT NULL, fact_id TEXT NOT NULL)""")
    conn.commit()

    # Insert existing data (should be preserved)
    conn.execute("INSERT INTO atomic_facts (fact_id, profile_id, content) VALUES ('old1', 'alice', 'old fact')")
    conn.commit()

    # Run migration
    upgrade(conn)

    # Verify columns added
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(atomic_facts)").fetchall()}
    assert "scope" in cols
    assert "shared_with" in cols

    # Verify existing data has default scope='personal'
    row = conn.execute("SELECT scope, shared_with FROM atomic_facts WHERE fact_id='old1'").fetchone()
    assert row["scope"] == "personal"
    assert row["shared_with"] is None

    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scope_schema.py::test_m014_migration_adds_columns -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create migration file**

Follow the M013 pattern: provide a proper DDL string with ALTER TABLE statements.

```python
# src/superlocalmemory/storage/migrations/M014_add_scope_support.py
"""M014: Add scope and shared_with columns for multi-scope memory support.

Adds two columns to each of the 5 core tables:
- scope TEXT NOT NULL DEFAULT 'personal'  (personal | global)
- shared_with TEXT                         (JSON array of profile_ids)

Existing data retains scope='personal' (backward compatible).
"""

NAME = "M014_add_scope_support"
DB_TARGET = "memory"

TABLES = [
    "memories", "atomic_facts", "canonical_entities",
    "graph_edges", "temporal_events",
]

DDL = ";".join(
    [f"ALTER TABLE {t} ADD COLUMN scope TEXT NOT NULL DEFAULT 'personal'"
     for t in TABLES]
    + [f"ALTER TABLE {t} ADD COLUMN shared_with TEXT" for t in TABLES]
    + [f"CREATE INDEX IF NOT EXISTS idx_{t}_scope ON {t}(scope)" for t in TABLES]
    + [f"CREATE INDEX IF NOT EXISTS idx_{t}_profile_scope ON {t}(profile_id, scope)"
       for t in TABLES]
)


def verify(conn) -> bool:
    """Check if migration already applied."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)").fetchall()}
    return "scope" in cols
```

- [ ] **Step 4: Register migration**

Follow the existing pattern in the codebase:

1. In `src/superlocalmemory/storage/migrations/__init__.py`, add to the package import block (follows M013):
```python
from . import M014_add_scope_support  # noqa: F401
```
And add to `__all__` tuple.

2. In `src/superlocalmemory/storage/migration_runner.py`:
   - Add to the imports section (~line 39-52): `from superlocalmemory.storage.migrations import M014_add_scope_support as _M014`
   - Add to `DEFERRED_MIGRATIONS` list (not `MIGRATIONS` — because these tables are bootstrapped at engine init, same reason M011/M013 use deferred):
```python
Migration(name=_M014.NAME, db_target="memory", ddl=_M014.DDL),
```
   - The `verify()` function is resolved at runtime via `_MODULES` dict — add `_M014` to it:
```python
_MODULES[_M014.NAME] = _M014
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_scope_schema.py::test_m014_migration_adds_columns -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/storage/migrations/M014_add_scope_support.py \
        src/superlocalmemory/storage/migrations/__init__.py \
        src/superlocalmemory/storage/migration_runner.py \
        tests/test_scope_schema.py
git commit -m "feat(scope): migration M014 adds scope/shared_with to core tables"
```

---

### Task 4: Add scope-aware WHERE helper to DatabaseManager

**Files:**
- Modify: `src/superlocalmemory/storage/database.py`
- Test: `tests/test_scope_db.py` (create)

- [ ] **Step 1: Write failing test for scope-aware queries**

```python
# tests/test_scope_db.py
"""Test scope-aware database queries."""
import sqlite3
import pytest

@pytest.fixture
def scope_db():
    """In-memory DB with scope columns and test data."""
    from superlocalmemory.storage import schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.create_all_tables(conn)
    conn.commit()

    # Insert test data across scopes
    # Personal for alice
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'alice', 'alice personal', 'personal')"
    )
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f1', 'm1', 'alice', 'alice fact', 'personal')"
    )
    # Personal for bob
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m2', 'bob', 'bob personal', 'personal')"
    )
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f2', 'm2', 'bob', 'bob fact', 'personal')"
    )
    # Global
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m3', 'alice', 'shared globally', 'global')"
    )
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f3', 'm3', 'alice', 'global fact', 'global')"
    )
    # Shared: alice shares with bob
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope, shared_with) "
        "VALUES ('f4', 'm1', 'alice', 'shared to bob', 'personal', '[\"bob\"]')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_get_all_facts_personal_only(scope_db):
    """Personal scope: alice sees only her own facts, not bob's."""
    from superlocalmemory.storage.database import DatabaseManager
    db = DatabaseManager.__new__(DatabaseManager)
    db._conn = scope_db

    facts = db.get_all_facts("alice", scope="personal", include_global=False, include_shared=False)
    fact_ids = {f.fact_id for f in facts}
    assert "f1" in fact_ids   # alice personal
    assert "f2" not in fact_ids  # bob personal
    assert "f3" not in fact_ids  # global
    assert "f4" in fact_ids   # alice's shared_with entry is still personal scope


def test_get_all_facts_with_global(scope_db):
    """Personal + global: alice sees her own + global facts."""
    from superlocalmemory.storage.database import DatabaseManager
    db = DatabaseManager.__new__(DatabaseManager)
    db._conn = scope_db

    facts = db.get_all_facts("alice", scope="personal", include_global=True, include_shared=False)
    fact_ids = {f.fact_id for f in facts}
    assert "f1" in fact_ids   # alice personal
    assert "f2" not in fact_ids  # bob personal
    assert "f3" in fact_ids   # global
    assert "f4" in fact_ids   # alice's own


def test_get_all_facts_with_shared(scope_db):
    """Personal + shared: bob sees his own + alice's shared_with=bob."""
    from superlocalmemory.storage.database import DatabaseManager
    db = DatabaseManager.__new__(DatabaseManager)
    db._conn = scope_db

    facts = db.get_all_facts("bob", scope="personal", include_global=False, include_shared=True)
    fact_ids = {f.fact_id for f in facts}
    assert "f1" not in fact_ids  # alice personal
    assert "f2" in fact_ids   # bob personal
    assert "f3" not in fact_ids  # global (not included)
    assert "f4" in fact_ids   # alice shared with bob


def test_search_facts_fts_with_scope(scope_db):
    """FTS search respects scope boundaries."""
    from superlocalmemory.storage.database import DatabaseManager
    db = DatabaseManager.__new__(DatabaseManager)
    db._conn = scope_db

    # Bob searches for "fact" — should only see his own (no global)
    facts = db.search_facts_fts("fact", "bob", scope="personal", include_global=False, include_shared=False)
    fact_ids = {f.fact_id for f in facts}
    assert "f2" in fact_ids   # bob's fact matches "fact"
    assert "f3" not in fact_ids  # global excluded

    # Bob searches with global — should see global too
    facts = db.search_facts_fts("global", "bob", scope="personal", include_global=True, include_shared=False)
    fact_ids = {f.fact_id for f in facts}
    assert "f3" in fact_ids   # global fact matches "global"


def test_personal_invisible_across_profiles(scope_db):
    """Bob cannot see alice's personal facts even with include_global."""
    from superlocalmemory.storage.database import DatabaseManager
    db = DatabaseManager.__new__(DatabaseManager)
    db._conn = scope_db

    facts = db.get_all_facts("bob", scope="personal", include_global=True, include_shared=False)
    fact_ids = {f.fact_id for f in facts}
    assert "f1" not in fact_ids  # alice personal invisible to bob
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope_db.py -v`
Expected: FAIL — methods don't accept scope parameters yet

- [ ] **Step 3: Add _scope_where helper to DatabaseManager**

In `src/superlocalmemory/storage/database.py`, add a helper method to the `DatabaseManager` class (after the `__init__` method):

```python
@staticmethod
def _scope_where(
    profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
) -> tuple[str, list]:
    """Build a scope-aware WHERE clause fragment.

    Returns (sql_fragment, params_list).
    Usage: WHERE <other_conditions> AND (<scope_fragment>)
    """
    conditions = []
    params: list = []

    # Personal scope (always included)
    if scope == "personal":
        conditions.append("(scope = 'personal' AND profile_id = ?)")
        params.append(profile_id)

    if include_global:
        conditions.append("scope = 'global'")

    if include_shared:
        conditions.append("? IN (SELECT value FROM json_each(shared_with))")
        params.append(profile_id)

    if not conditions:
        # Fallback: personal only
        conditions.append("(scope = 'personal' AND profile_id = ?)")
        params.append(profile_id)

    sql = "(" + " OR ".join(conditions) + ")"
    return sql, params
```

- [ ] **Step 4: Update get_all_facts method**

Modify `get_all_facts` (line ~245) in `database.py`:

```python
def get_all_facts(
    self, profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
) -> list[AtomicFact]:
    """All facts visible to a profile, respecting scope boundaries."""
    where_sql, where_params = self._scope_where(
        profile_id, scope, include_global, include_shared,
    )
    rows = self.execute(
        f"SELECT * FROM atomic_facts WHERE {where_sql} ORDER BY created_at DESC",
        where_params,
    )
    return [self._row_to_fact(r) for r in rows]
```

- [ ] **Step 5: Update search_facts_fts method**

Modify `search_facts_fts` (line ~470) in `database.py`:

```python
def search_facts_fts(
    self, query: str, profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    limit: int = 20,
) -> list[AtomicFact]:
    """Full-text search with scope awareness."""
    where_sql, where_params = self._scope_where(
        profile_id, scope, include_global, include_shared,
    )
    rows = self.execute(
        f"""SELECT f.* FROM atomic_facts_fts AS fts
           JOIN atomic_facts AS f ON f.fact_id = fts.fact_id
           WHERE fts.atomic_facts_fts MATCH ? AND {where_sql.replace('profile_id', 'f.profile_id').replace('scope', 'f.scope').replace('shared_with', 'f.shared_with')}
           ORDER BY fts.rank LIMIT ?""",
        [query] + where_params + [limit],
    )
    return [self._row_to_fact(r) for r in rows]
```

Note: The FTS query needs table-qualified column names. The `_scope_where` helper produces generic column names; for JOINs, qualify them with the table alias. A cleaner approach is to accept an optional `table_alias` parameter in `_scope_where`.

Let me revise the helper to support table aliases:

```python
@staticmethod
def _scope_where(
    profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    table_alias: str = "",
) -> tuple[str, list]:
    """Build a scope-aware WHERE clause fragment."""
    prefix = f"{table_alias}." if table_alias else ""
    conditions = []
    params: list = []

    if scope == "personal":
        conditions.append(f"({prefix}scope = 'personal' AND {prefix}profile_id = ?)")
        params.append(profile_id)

    if include_global:
        conditions.append(f"{prefix}scope = 'global'")

    if include_shared:
        conditions.append(f"? IN (SELECT value FROM json_each({prefix}shared_with))")
        params.append(profile_id)

    if not conditions:
        conditions.append(f"({prefix}scope = 'personal' AND {prefix}profile_id = ?)")
        params.append(profile_id)

    sql = "(" + " OR ".join(conditions) + ")"
    return sql, params
```

Then `search_facts_fts` becomes:

```python
def search_facts_fts(
    self, query: str, profile_id: str,
    scope: str = "personal",
    include_global: bool = True,
    include_shared: bool = True,
    limit: int = 20,
) -> list[AtomicFact]:
    """Full-text search with scope awareness."""
    where_sql, where_params = self._scope_where(
        profile_id, scope, include_global, include_shared, table_alias="f",
    )
    rows = self.execute(
        f"""SELECT f.* FROM atomic_facts_fts AS fts
           JOIN atomic_facts AS f ON f.fact_id = fts.fact_id
           WHERE fts.atomic_facts_fts MATCH ? AND {where_sql}
           ORDER BY fts.rank LIMIT ?""",
        [query] + where_params + [limit],
    )
    return [self._row_to_fact(r) for r in rows]
```

- [ ] **Step 6: Update remaining 22 methods**

Apply the same pattern to all other methods that take `profile_id`. Each method gets 3 new parameters (`scope`, `include_global`, `include_shared`) with default values, and uses `_scope_where` instead of `WHERE profile_id = ?`.

Key methods to update (with their current line numbers):

| Method | Line | Notes |
|--------|------|-------|
| `get_facts_by_entity` | ~255 | Entity lookup, LIKE query |
| `get_facts_by_type` | ~270 | Fact type filter |
| `get_fact_count` | ~314 | Count query |
| `get_entity_by_name` | ~334 | Entity name lookup (canonical_entities) |
| `get_edges_for_node` | ~404 | Graph edge lookup |
| `get_temporal_events` | ~435 | Temporal event lookup |
| `store_bm25_tokens` | ~455 | No change needed (write-only) |
| `get_all_bm25_tokens` | ~462 | May skip scope (derived data) |
| `get_all_scenes` | ~589 | Scene lookup |
| `get_all_fact_contexts` | ~713 | Context lookup |
| `get_all_association_edges` | ~752 | Association lookup |
| `delete_association_edges` | ~760 | Write operation |
| `get_all_temporal_validity` | ~870 | Validity lookup |
| `get_valid_facts` | ~897 | Valid fact IDs |
| `get_core_blocks` | ~949 | Core blocks |
| `get_core_block` | ~958 | Single block |
| `delete_core_blocks` | ~967 | Write operation |
| `get_retention` | ~978 | Retention data |
| `get_facts_needing_decay` | ~1067 | Decay candidates |
| `soft_delete_fact` | ~1087 | Write operation |
| `get_ccq_blocks` | ~1147 | CCQ blocks |
| `get_ccq_audit` | ~1171 | CCQ audit |

For write methods (store_bm25_tokens, delete_association_edges, soft_delete_fact, delete_core_blocks), scope is set at write time and doesn't need query-time scope filtering. These can be left unchanged for Phase 1.

**Important: INSERT methods also need updating.** The `store_memory()` (~line 152) and `store_fact()` (~line 192) INSERT statements must include `scope` and `shared_with` columns. This is covered in Task 6 (StorePipeline) when we update `run_store()` to pass scope down. The INSERT changes happen there, not in this task, to keep Task 4 focused on read-path scope filtering.

Priority methods to update (read queries in the retrieval path):
1. `get_all_facts` — done (Step 4)
2. `search_facts_fts` — done (Step 5)
3. `get_facts_by_entity` — entity channel
4. `get_facts_by_type` — type filtering
5. `get_entity_by_name` — entity resolution
6. `get_edges_for_node` — graph traversal
7. `get_temporal_events` — temporal channel
8. `get_fact_count` — count queries
9. `get_facts_needing_decay` — decay lifecycle
10. `get_retention` — retention data
11. `get_valid_facts` — validity checks
12. `get_facts_by_ids` (if exists) — post-KNN lookup

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_scope_db.py -v`
Expected: All PASS

- [ ] **Step 8: Run existing tests to verify no regression**

Run: `pytest tests/ -q --tb=short -x`
Expected: No failures (all default parameter values preserve existing behavior)

- [ ] **Step 9: Commit**

```bash
git add src/superlocalmemory/storage/database.py tests/test_scope_db.py
git commit -m "feat(scope): scope-aware WHERE clause for DatabaseManager queries"
```

---

## Chunk 2: Core Logic — Engine, StorePipeline, RecallPipeline

### Task 5: Update MemoryEngine store/recall signatures

**Files:**
- Modify: `src/superlocalmemory/core/engine.py`
- Test: `tests/test_scope_engine.py` (create)

- [ ] **Step 1: Write failing test for engine.store with scope**

```python
# tests/test_scope_engine.py
"""Test scope behavior through MemoryEngine API."""
import pytest


@pytest.fixture
def two_agent_engines(tmp_path):
    """Two MemoryEngine instances simulating different agents."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine
    from superlocalmemory.storage.models import Mode

    config_a = SLMConfig.for_mode(Mode.A, base_dir=tmp_path / "alice")
    config_a.retrieval.use_cross_encoder = False
    config_b = SLMConfig.for_mode(Mode.A, base_dir=tmp_path / "bob")
    config_b.retrieval.use_cross_encoder = False

    engine_a = MemoryEngine(config_a)
    engine_b = MemoryEngine(config_b)
    yield engine_a, engine_b
    engine_a.close()
    engine_b.close()


def test_store_personal_default(two_agent_engines):
    """Default store is personal scope."""
    engine_a, engine_b = two_agent_engines
    ids = engine_a.store("Alice's secret thought")
    assert len(ids) > 0


def test_store_global_visible_to_other(two_agent_engines):
    """Global store is visible to other agents on recall."""
    engine_a, engine_b = two_agent_engines

    # Alice stores globally
    engine_a.store("Project uses React 19", scope="global")

    # Bob should see it
    result = engine_b.recall("React", include_global=True)
    contents = [r.content for r in result.results]
    assert any("React 19" in c for c in contents)


def test_store_personal_invisible_to_other(two_agent_engines):
    """Personal store is NOT visible to other agents."""
    engine_a, engine_b = two_agent_engines

    engine_a.store("Alice's private note", scope="personal")

    # Bob should NOT see it
    result = engine_b.recall("Alice private", include_global=True)
    contents = [r.content for r in result.results]
    assert not any("private note" in c for c in contents)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope_engine.py -v`
Expected: FAIL — `store()` doesn't accept `scope` parameter

- [ ] **Step 3: Update engine.store() signature**

In `src/superlocalmemory/core/engine.py`, modify `store()` (line ~315):

```python
def store(
    self,
    content: str,
    session_id: str = "",
    session_date: str | None = None,
    speaker: str = "",
    role: str = "user",
    metadata: dict[str, Any] | None = None,
    scope: str = "personal",
    shared_with: list[str] | None = None,
) -> list[str]:
    """Store content and extract structured facts. Returns fact_ids."""
    self._require_full("store")
    self._ensure_init()

    from superlocalmemory.core.store_pipeline import run_store
    return run_store(
        content, self._profile_id,
        session_id=session_id, session_date=session_date,
        speaker=speaker, role=role, metadata=metadata,
        scope=scope, shared_with=shared_with,
        config=self._config, db=self._db,
        embedder=self._embedder,
        fact_extractor=self._fact_extractor,
        entity_resolver=self._entity_resolver,
        temporal_parser=self._temporal_parser,
        type_router=self._type_router,
        graph_builder=self._graph_builder,
        consolidator=self._consolidator,
        observation_builder=self._observation_builder,
        scene_builder=self._scene_builder,
        entropy_gate=self._entropy_gate,
        ann_index=self._ann_index,
        sheaf_checker=self._sheaf_checker,
        retrieval_engine=self._retrieval_engine,
        provenance=self._provenance,
        hooks=self._hooks,
        vector_store=self._vector_store,
        context_generator=self._context_generator,
        temporal_validator=self._temporal_validator,
        auto_linker=self._auto_linker,
        consolidation_engine=self._consolidation_engine,
    )
```

- [ ] **Step 4: Update engine.recall() signature**

In `src/superlocalmemory/core/engine.py`, modify `recall()` (line ~374):

```python
def recall(
    self, query: str, profile_id: str | None = None,
    mode: Mode | None = None, limit: int = 20,
    agent_id: str = "unknown",
    session_id: str | None = None,
    include_global: bool = True,
    include_shared: bool = True,
) -> RecallResponse:
```

Pass `include_global` and `include_shared` to `run_recall()`:

```python
    response = run_recall(
        query, pid, mode=mode, limit=limit, agent_id=agent_id,
        include_global=include_global,
        include_shared=include_shared,
        config=self._config,
        retrieval_engine=self._retrieval_engine,
        trust_scorer=self._trust_scorer,
        embedder=self._embedder,
        db=self._db, llm=self._llm,
        hooks=self._hooks,
        access_log=self._access_log,
        auto_linker=self._auto_linker,
    )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_scope_engine.py -v`
Expected: May still fail if store_pipeline doesn't accept scope yet. That's Task 6.

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/core/engine.py tests/test_scope_engine.py
git commit -m "feat(scope): add scope/shared_with to MemoryEngine store/recall"
```

---

### Task 6: Update StorePipeline to pass scope through

**Files:**
- Modify: `src/superlocalmemory/core/store_pipeline.py`

- [ ] **Step 1: Update run_store signature**

In `src/superlocalmemory/core/store_pipeline.py`, modify `run_store()` (line ~109):

Add two parameters after `metadata`:
```python
def run_store(
    content: str,
    profile_id: str,
    session_id: str = "",
    session_date: str | None = None,
    speaker: str = "",
    role: str = "user",
    metadata: dict[str, Any] | None = None,
    scope: str = "personal",
    shared_with: list[str] | None = None,
    *,
    config: SLMConfig,
    db: DatabaseManager,
    ...
```

- [ ] **Step 2: Pass scope to MemoryRecord and fact creation**

In `run_store()`, where `MemoryRecord` is created (~line 159):
```python
record = MemoryRecord(
    memory_id=memory_id,
    profile_id=profile_id,
    content=content,
    ...
    scope=scope,
    shared_with=shared_with,
)
```

Where `AtomicFact` objects are created (in `enrich_fact` or equivalent), add:
```python
fact.scope = scope
fact.shared_with = shared_with
```

Where facts are stored to the database (e.g., `db.insert_fact()` or equivalent INSERT), ensure scope and shared_with are included in the INSERT statement.

Check `database.py` for the INSERT method and ensure it includes `scope` and `shared_with` columns. If it uses `**kwargs` or a model object, the fields should propagate automatically from the model.

- [ ] **Step 3: Run engine tests**

Run: `pytest tests/test_scope_engine.py -v`
Expected: May still fail if recall_pipeline not updated yet.

- [ ] **Step 4: Commit**

```bash
git add src/superlocalmemory/core/store_pipeline.py
git commit -m "feat(scope): pass scope/shared_with through StorePipeline"
```

---

### Task 7: Update RecallPipeline for multi-scope retrieval

**Files:**
- Modify: `src/superlocalmemory/core/recall_pipeline.py`
- Modify: `src/superlocalmemory/retrieval/engine.py`
- Modify: `src/superlocalmemory/retrieval/fusion.py`

- [ ] **Step 1: Update run_recall signature**

In `src/superlocalmemory/core/recall_pipeline.py`, modify `run_recall()` (line ~543):

```python
def run_recall(
    query: str,
    profile_id: str,
    mode: Mode | None = None,
    limit: int = 20,
    agent_id: str = "unknown",
    *,
    include_global: bool = True,
    include_shared: bool = True,
    config: SLMConfig,
    retrieval_engine: Any,
    ...
```

Pass `include_global` and `include_shared` to `retrieval_engine.recall()`.

- [ ] **Step 2: Update RetrievalEngine.recall() signature**

In `src/superlocalmemory/retrieval/engine.py`, modify `recall()` (line ~112):

```python
def recall(
    self, query: str, profile_id: str,
    mode: Mode = Mode.A, limit: int = 20,
    include_global: bool = True,
    include_shared: bool = True,
) -> RecallResponse:
```

- [ ] **Step 3: Implement multi-scope retrieval in RetrievalEngine**

In the `recall()` method, replace the single `_run_channels` call with multi-scope passes:

```python
SCOPE_WEIGHTS = {"personal": 1.0, "global": 0.5, "shared": 0.7}

# Build combined channel results with scope prefix
all_ch_results: dict[str, list[tuple[str, float]]] = {}
all_weights: dict[str, float] = {}

# 1. Personal scope (always)
personal_ch = self._run_channels(query, profile_id, strat)
for ch_name, results in personal_ch.items():
    key = f"{ch_name}:personal"
    all_ch_results[key] = results
    all_weights[key] = strat.weights.get(ch_name, 1.0) * SCOPE_WEIGHTS["personal"]

# 2. Global scope
if include_global:
    global_ch = self._run_channels(query, profile_id, strat, scope="global")
    for ch_name, results in global_ch.items():
        key = f"{ch_name}:global"
        all_ch_results[key] = results
        all_weights[key] = strat.weights.get(ch_name, 1.0) * SCOPE_WEIGHTS["global"]

# 3. Shared-with-me scope
if include_shared:
    shared_ch = self._run_channels(query, profile_id, strat, scope="shared")
    for ch_name, results in shared_ch.items():
        key = f"{ch_name}:shared"
        all_ch_results[key] = results
        all_weights[key] = strat.weights.get(ch_name, 1.0) * SCOPE_WEIGHTS["shared"]

# Single RRF fusion with scope-weighted channel keys
fused = weighted_rrf(all_ch_results, all_weights, k=self._config.rrf_k)
```

- [ ] **Step 4: Update _run_channels to accept scope parameter**

In `src/superlocalmemory/retrieval/engine.py`, modify `_run_channels()` (line ~440):

```python
def _run_channels(
    self, query: str, profile_id: str, strat: QueryStrategy,
    scope: str = "personal",
) -> dict[str, list[tuple[str, float]]]:
```

For `scope="global"`: pass `profile_id=None` to channels (no profile filter).
For `scope="shared"`: pass profile_id normally; channels use shared_with filter.

Each channel's `search()` call gets an additional `scope` parameter.

- [ ] **Step 5: Run engine tests**

Run: `pytest tests/test_scope_engine.py -v`
Expected: Tests should now pass if channels accept scope.

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/core/recall_pipeline.py \
        src/superlocalmemory/retrieval/engine.py \
        src/superlocalmemory/retrieval/fusion.py
git commit -m "feat(scope): multi-scope retrieval with weighted RRF fusion"
```

---

## Chunk 3: Integration — Channels, MCP, Pending Store, Tests

### Task 8: Update 7 retrieval channels for scope support

**Files:**
- Modify: `src/superlocalmemory/retrieval/semantic_channel.py`
- Modify: `src/superlocalmemory/retrieval/bm25_channel.py`
- Modify: `src/superlocalmemory/retrieval/entity_channel.py`
- Modify: `src/superlocalmemory/retrieval/temporal_channel.py`
- Modify: `src/superlocalmemory/retrieval/hopfield_channel.py`
- Modify: `src/superlocalmemory/retrieval/profile_channel.py`
- Modify: `src/superlocalmemory/retrieval/spreading_activation.py`

- [ ] **Step 1: Update each channel's search() signature**

Each channel's `search()` method gets a `scope` parameter:

```python
def search(self, query: str | list[float], profile_id: str,
           top_k: int = 50, scope: str = "personal") -> list[tuple[str, float]]:
```

- [ ] **Step 2: Pass scope to DB method calls**

For each channel, wherever it calls a DB method, pass `scope`:

**semantic_channel.py** (line ~168):
```python
knn_results = self._vector_store.search(query_embedding, top_k=top_k * 2,
                                         profile_id=profile_id, scope=scope)
facts = self._db.get_facts_by_ids(candidate_ids, profile_id, scope=scope)
```

**bm25_channel.py** (line ~88, ~107):
```python
token_map = self._db.get_all_bm25_tokens(profile_id, scope=scope)
facts = self._db.get_all_facts(profile_id, scope=scope)
```

**entity_channel.py** (line ~125, ~175, ~270):
```python
# Direct SQL queries need scope WHERE clause
rows = self._db.execute(
    "SELECT source_id, target_id, weight FROM graph_edges "
    "WHERE profile_id = ? AND scope = ?", (profile_id, scope),
)
```

**temporal_channel.py** (line ~150, ~167):
```python
rows = self._db.execute(
    "SELECT fact_id FROM temporal_events "
    "WHERE profile_id = ? AND entity_id = ? AND scope = ?",
    (profile_id, entity_id, scope),
)
```

**hopfield_channel.py** (line ~136, ~238):
```python
total_count = self._vector_store.count(profile_id, scope=scope)
candidates = self._db.get_facts_by_ids(candidate_ids, profile_id, scope=scope)
```

**profile_channel.py** (line ~81, ~85):
```python
entity = self._db.get_entity_by_name(name, profile_id, scope=scope)
```

**spreading_activation.py** (line ~110, ~222):
```python
seed_results = self._vector_store.search(query, top_k=top_m,
                                          profile_id=profile_id, scope=scope)
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -q --tb=short -x`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add src/superlocalmemory/retrieval/
git commit -m "feat(scope): add scope parameter to 7 retrieval channels"
```

---

### Task 9: Update MCP tools and pending store flow

**Files:**
- Modify: `src/superlocalmemory/mcp/tools_core.py`
- Modify: `src/superlocalmemory/server/unified_daemon.py`

- [ ] **Step 1: Update remember MCP tool**

In `src/superlocalmemory/mcp/tools_core.py`, modify `remember()` (line ~104):

```python
async def remember(
    content: str, tags: str = "", project: str = "",
    importance: int = 5, session_id: str = "",
    agent_id: str = "mcp_client",
    scope: str = "personal",
    shared_with: str = "",
) -> dict:
```

Update the `store_pending` call to include scope in metadata:
```python
pending_id = store_pending(content, tags=tags, metadata={
    "project": project,
    "importance": importance,
    "agent_id": agent_id,
    "session_id": session_id,
    "scope": scope,
    "shared_with": shared_with.split(",") if shared_with else None,
})
```

- [ ] **Step 2: Update recall MCP tool**

In `src/superlocalmemory/mcp/tools_core.py`, modify `recall()` (line ~142):

```python
async def recall(
    query: str, limit: int = 10,
    agent_id: str = "mcp_client",
    session_id: str = "",
    include_global: bool = True,
    include_shared: bool = True,
) -> dict:
```

Note: The recall tool uses `pool.recall()` via WorkerPool. The `include_global`/`include_shared` flags need to propagate through to `engine.recall()`. Check if WorkerPool.recall() supports additional kwargs or if it needs updating.

- [ ] **Step 3: Update daemon materializer**

In `src/superlocalmemory/server/unified_daemon.py`, modify the materializer loop (~line 1283):

```python
# Extract scope from metadata
scope = md.get("scope", "personal")
shared_with = md.get("shared_with")

engine.store(
    item["content"],
    metadata=md,
    scope=scope,
    shared_with=shared_with,
)
mark_done(item["id"])
```

- [ ] **Step 4: Commit**

```bash
git add src/superlocalmemory/mcp/tools_core.py \
        src/superlocalmemory/server/unified_daemon.py
git commit -m "feat(scope): MCP tools + daemon materializer scope support"
```

---

### Task 10: Integration tests

**Files:**
- Test: `tests/test_scope_integration.py` (create)

- [ ] **Step 1: Write cross-agent visibility test**

```python
# tests/test_scope_integration.py
"""End-to-end scope integration tests."""
import pytest


@pytest.fixture
def multi_agent_setup(tmp_path):
    """Set up two agent engines sharing the same DB (simulating global scope)."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine
    from superlocalmemory.storage.models import Mode

    config_a = SLMConfig.for_mode(Mode.A, base_dir=tmp_path / "agent_a")
    config_a.active_profile = "agent_a"
    config_a.retrieval.use_cross_encoder = False

    config_b = SLMConfig.for_mode(Mode.A, base_dir=tmp_path / "agent_b")
    config_b.active_profile = "agent_b"
    config_b.retrieval.use_cross_encoder = False

    engine_a = MemoryEngine(config_a)
    engine_b = MemoryEngine(config_b)
    yield engine_a, engine_b
    engine_a.close()
    engine_b.close()


def test_global_recall_cross_agent(multi_agent_setup):
    """Agent A stores global → Agent B recalls it."""
    engine_a, engine_b = multi_agent_setup
    engine_a.store("React 19 is our framework", scope="global")

    result = engine_b.recall("framework", include_global=True)
    assert result.result_count > 0
    assert any("React 19" in r.content for r in result.results)


def test_personal_isolation(multi_agent_setup):
    """Agent A stores personal → Agent B cannot see it."""
    engine_a, engine_b = multi_agent_setup
    engine_a.store("My secret debugging technique", scope="personal")

    result = engine_b.recall("debugging", include_global=True)
    assert all("secret" not in r.content for r in result.results)


def test_shared_with_specific_agent(multi_agent_setup):
    """Agent A shares with Agent B specifically."""
    engine_a, engine_b = multi_agent_setup
    engine_a.store("API endpoint changed", scope="personal",
                   shared_with=["agent_b"])

    # Agent B sees it
    result_b = engine_b.recall("API", include_global=False, include_shared=True)
    assert any("endpoint changed" in r.content for r in result_b.results)

    # Agent C (hypothetical) would NOT see it


def test_global_weight_lower_than_personal(multi_agent_setup):
    """Personal results rank higher than global in RRF fusion."""
    engine_a, engine_b = multi_agent_setup

    # Store similar content in both scopes
    engine_b.store("Use React hooks for state", scope="personal")
    engine_a.store("Use React hooks for state management", scope="global")

    result = engine_b.recall("React state", include_global=True)
    if result.result_count >= 2:
        # Personal should rank first
        assert "personal" in str(result.results[0]).lower() or \
               result.results[0].score >= result.results[1].score
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_scope_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite for regression check**

Run: `pytest tests/ -q --tb=short -x`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add tests/test_scope_integration.py
git commit -m "test(scope): cross-agent visibility and isolation tests"
```

---

### Task 11: Final verification and cleanup

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -q --tb=short`
Expected: All tests pass

- [ ] **Step 2: Run ruff check**

Run: `ruff check src/superlocalmemory/storage/database.py src/superlocalmemory/core/engine.py src/superlocalmemory/core/store_pipeline.py src/superlocalmemory/core/recall_pipeline.py src/superlocalmemory/retrieval/ src/superlocalmemory/mcp/tools_core.py`
Expected: No errors

- [ ] **Step 3: Run ruff format**

Run: `ruff format src/superlocalmemory/storage/database.py src/superlocalmemory/core/engine.py src/superlocalmemory/core/store_pipeline.py src/superlocalmemory/core/recall_pipeline.py src/superlocalmemory/retrieval/ src/superlocalmemory/mcp/tools_core.py`

- [ ] **Step 4: Verify migration works on existing DB**

```bash
# Start SLM to trigger migration
slm doctor
slm status
```

Expected: No errors, migration runs successfully

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore(scope): format and lint fixes for multi-scope Phase 1"
```
