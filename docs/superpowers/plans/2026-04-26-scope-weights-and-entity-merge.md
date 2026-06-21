# Scope Weights Configurable + Entity Merge Tool — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multi-scope RRF weights configurable via SLMConfig (instead of hardcoded), and add a `slm entity merge` CLI command + MCP tool for consolidating pre-Phase 3 personal entities into global entities.

**Architecture:** Two independent features. Feature 1 adds a `ScopeWeights` dataclass to `SLMConfig` and reads it from `RetrievalEngine`. Feature 2 adds a `merge_entities()` method to `DatabaseManager`, wires it through `MemoryEngine` and exposes it as CLI command + MCP tool. Both are additive — no breaking changes.

**Tech Stack:** Python 3.11+, SQLite, pytest, argparse

---

## Codebase Conventions (READ FIRST)

These patterns were verified against the actual codebase. Follow them exactly.

### MemoryEngine constructor
```python
# engine.py:53-61 — takes (config, capabilities), NOT profile_id
engine = MemoryEngine(config=config)
# Profile comes from config.active_profile
```

### CLI command pattern (commands.py)
- Each command is `cmd_<name>(args: Namespace)`
- `dispatch()` at line 77 uses a **handlers dict**, not if/elif
- Subcommand routing uses wrapper functions like `_cmd_db_dispatch(args)` (line 23)
- JSON output: `from superlocalmemory.cli.json_output import json_print` then `json_print("name", data=...)`
- Engine construction in commands: `config = SLMConfig.load()` / `engine = MemoryEngine(config)` / `engine.initialize()`
- `--json` is registered as `action="store_true"` (no `dest=`), checked via `getattr(args, 'json', False)`

### MCP tool registration pattern
- Tools are registered via `register_*_tools(server, get_engine)` functions in separate modules
- `get_engine` is a zero-arg callable (singleton factory), defined at `server.py:39`
- Tool functions call `engine = get_engine()` then `engine.profile_id`
- Return dicts: `{"success": True, "data": ...}` or `{"success": False, "error": ...}`
- Essential tools go in `_ESSENTIAL_TOOLS` set at `server.py:84`

### DatabaseManager
- `initialize(schema_module)` requires the schema module: `from superlocalmemory.storage import schema; db.initialize(schema)`
- `transaction()` context manager at line 236 — use for atomic multi-step operations
- `execute()` returns `list[Row]` — creates a new connection per call (no persistent `self._conn`)
- FK constraints on `entity_id` do NOT have `ON DELETE CASCADE` — just `FOREIGN KEY (entity_id) REFERENCES canonical_entities (entity_id)`

---

## File Structure

### Feature 1: Scope Weights Configurable

| File | Action | Responsibility |
|------|--------|---------------|
| `src/superlocalmemory/core/config.py` | Modify | Add `ScopeWeights` dataclass, integrate into `SLMConfig` |
| `src/superlocalmemory/retrieval/engine.py` | Modify | Read weights from config instead of hardcoded dict |
| `src/superlocalmemory/core/engine_wiring.py` | Modify | Pass scope_weights from SLMConfig to RetrievalEngine |
| `tests/test_scope_weights.py` | Create | Tests for ScopeWeights config + retrieval engine integration |

### Feature 2: Entity Merge Tool

| File | Action | Responsibility |
|------|--------|---------------|
| `src/superlocalmemory/storage/database.py` | Modify | Add `get_entities_by_scope()`, `merge_entities()` methods |
| `src/superlocalmemory/core/engine.py` | Modify | Add `merge_entities()` facade method |
| `src/superlocalmemory/cli/commands.py` | Modify | Add `_cmd_entity_dispatch()`, `cmd_entity_merge()`, `cmd_entity_list()` |
| `src/superlocalmemory/cli/main.py` | Modify | Register `entity` subparser with `merge` and `list` subcommands |
| `src/superlocalmemory/mcp/tools_core.py` | Modify | Add `merge_entities` MCP tool in `register_core_tools()` |
| `src/superlocalmemory/mcp/server.py` | Modify | Add `"merge_entities"` to `_ESSENTIAL_TOOLS` |
| `tests/test_entity_merge.py` | Create | Tests for entity merge logic |

---

## Chunk 1: Scope Weights Configurable (Tasks 1–4)

### Task 1: Add ScopeWeights dataclass to config.py

**Files:**
- Modify: `src/superlocalmemory/core/config.py:93-114` (after ChannelWeights class)
- Create: `tests/test_scope_weights.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scope_weights.py
"""Tests for ScopeWeights configuration and retrieval engine integration."""

from superlocalmemory.core.config import SLMConfig, ScopeWeights


def test_scope_weights_defaults():
    sw = ScopeWeights()
    assert sw.personal == 1.0
    assert sw.shared == 0.7
    assert sw.global_ == 0.5


def test_scope_weights_custom():
    sw = ScopeWeights(personal=1.2, shared=0.8, global_=0.6)
    assert sw.personal == 1.2
    assert sw.shared == 0.8
    assert sw.global_ == 0.6


def test_scope_weights_as_dict():
    sw = ScopeWeights()
    d = sw.as_dict()
    assert d == {"personal": 1.0, "shared": 0.7, "global": 0.5}


def test_slmconfig_has_scope_weights():
    config = SLMConfig.default()
    assert hasattr(config, "scope_weights")
    assert config.scope_weights.personal == 1.0
    assert config.scope_weights.global_ == 0.5


def test_scope_weights_validation():
    import pytest
    with pytest.raises(ValueError, match="non-negative"):
        ScopeWeights(personal=-0.1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scope_weights.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScopeWeights'`

- [ ] **Step 3: Write minimal implementation**

Add after `ChannelWeights` class (after line ~113) in `src/superlocalmemory/core/config.py`:

```python
@dataclass
class ScopeWeights:
    """RRF fusion weights for multi-scope retrieval.

    Personal scope has highest weight (1.0) so the agent's own memories
    rank above global/shared ones. Global (0.5) provides shared knowledge
    at lower priority. Shared (0.7) bridges between agents.
    """

    personal: float = 1.0
    shared: float = 0.7
    global_: float = 0.5  # trailing underscore avoids Python keyword

    def __post_init__(self) -> None:
        for name in ("personal", "shared", "global_"):
            val = getattr(self, name)
            if val < 0:
                raise ValueError(f"ScopeWeights values must be non-negative, got {name}={val}")

    def as_dict(self) -> dict[str, float]:
        return {"personal": self.personal, "shared": self.shared, "global": self.global_}
```

Add `scope_weights` field to `SLMConfig` class (around line 591, after `channel_weights`):

```python
    scope_weights: ScopeWeights = field(default_factory=ScopeWeights)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scope_weights.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/core/config.py tests/test_scope_weights.py
git commit -m "feat(config): add ScopeWeights dataclass for configurable multi-scope RRF weights"
```

---

### Task 2: Wire ScopeWeights into RetrievalEngine

**Files:**
- Modify: `src/superlocalmemory/retrieval/engine.py:64-97` (constructor) and `engine.py:168` (hardcoded dict)
- Modify: `tests/test_scope_weights.py` (add integration test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scope_weights.py — append

def test_retrieval_engine_uses_scope_weights():
    """RetrievalEngine reads scope weights from ScopeWeights config."""
    from superlocalmemory.core.config import ScopeWeights
    from unittest.mock import MagicMock
    from superlocalmemory.retrieval.engine import RetrievalEngine

    sw = ScopeWeights(personal=1.5, shared=0.3, global_=0.1)

    channels = {name: MagicMock() for name in
                ["semantic", "bm25", "entity_graph", "temporal", "hopfield", "spreading_activation"]}
    for ch in channels.values():
        ch.search.return_value = []
        ch.ensure_loaded = MagicMock()

    db = MagicMock()
    db.execute.return_value = []
    db.get_all_bm25_tokens.return_value = {}
    db.get_all_facts.return_value = []

    config = MagicMock()
    config.rrf_k = 15
    config.disabled_channels = []
    config.use_cross_encoder = False

    engine = RetrievalEngine(
        db=db, config=config, channels=channels, scope_weights=sw,
    )
    assert engine._scope_weights.personal == 1.5
    assert engine._scope_weights.global_ == 0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scope_weights.py::test_retrieval_engine_uses_scope_weights -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'scope_weights'`

- [ ] **Step 3: Write minimal implementation**

In `src/superlocalmemory/retrieval/engine.py`, update the import:

```python
from superlocalmemory.core.config import ChannelWeights, RetrievalConfig, ScopeWeights
```

Add `scope_weights` parameter to `__init__` (after `skill_tags` param):

```python
        scope_weights: ScopeWeights | None = None,
```

Store as instance attribute (after `self._skill_tags`):

```python
        self._scope_weights = scope_weights or ScopeWeights()
```

Replace the hardcoded dict at line 168:

```python
        # BEFORE:
        # _SCOPE_WEIGHTS = {"personal": 1.0, "global": 0.5, "shared": 0.7}

        # AFTER:
        _SCOPE_WEIGHTS = self._scope_weights.as_dict()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scope_weights.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/retrieval/engine.py tests/test_scope_weights.py
git commit -m "feat(retrieval): wire ScopeWeights into RetrievalEngine, remove hardcoded scope weights"
```

---

### Task 3: Wire ScopeWeights through engine_wiring.py

**Files:**
- Modify: `src/superlocalmemory/core/engine_wiring.py`

- [ ] **Step 1: Find the RetrievalEngine construction site**

Run: `grep -n "RetrievalEngine(" src/superlocalmemory/core/engine_wiring.py`

- [ ] **Step 2: Add scope_weights parameter**

Add `scope_weights=config.scope_weights` to the `RetrievalEngine(...)` constructor call found in Step 1.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -q --tb=short -x`
Expected: All existing tests pass (no regressions)

- [ ] **Step 4: Commit**

```bash
git add src/superlocalmemory/core/engine_wiring.py
git commit -m "feat(wiring): pass ScopeWeights from SLMConfig to RetrievalEngine"
```

---

### Task 4: Persist scope weights in config.json

**Files:**
- Modify: `src/superlocalmemory/core/config.py` — `SLMConfig.load()` and `SLMConfig.save()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scope_weights.py — append

import json


def test_scope_weights_persist_load_save(tmp_path):
    """ScopeWeights round-trips through JSON config."""
    config = SLMConfig.default()
    config.scope_weights = ScopeWeights(personal=1.3, shared=0.6, global_=0.4)

    config_path = tmp_path / "config.json"
    config.base_dir = tmp_path
    config.save(config_path)

    data = json.loads(config_path.read_text())
    assert "scope_weights" in data
    assert data["scope_weights"]["personal"] == 1.3
    assert data["scope_weights"]["global_"] == 0.4

    loaded = SLMConfig.load(config_path)
    assert loaded.scope_weights.personal == 1.3
    assert loaded.scope_weights.shared == 0.6
    assert loaded.scope_weights.global_ == 0.4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scope_weights.py::test_scope_weights_persist_load_save -v`
Expected: FAIL — scope_weights section missing from saved JSON

- [ ] **Step 3: Write minimal implementation**

In `SLMConfig.save()`, add after the evolution config section:

```python
        # Multi-scope memory: scope weights
        data["scope_weights"] = {
            "personal": self.scope_weights.personal,
            "shared": self.scope_weights.shared,
            "global_": self.scope_weights.global_,
        }
```

In `SLMConfig.load()`, add after the evolution config section:

```python
        # Multi-scope memory: scope weights
        sw = data.get("scope_weights", {})
        if sw:
            config.scope_weights = ScopeWeights(**{
                k: v for k, v in sw.items()
                if k in ScopeWeights.__dataclass_fields__
            })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scope_weights.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -q --tb=short -x`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/core/config.py tests/test_scope_weights.py
git commit -m "feat(config): persist ScopeWeights in config.json load/save"
```

---

## Chunk 2: Entity Merge — Storage Layer (Tasks 5–6)

### Task 5: Add entity query + merge methods to DatabaseManager

**Files:**
- Modify: `src/superlocalmemory/storage/database.py` (add `get_entities_by_scope()`, `merge_entities()`)
- Create: `tests/test_entity_merge.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entity_merge.py
"""Tests for entity merge functionality."""

import json
import pytest
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import CanonicalEntity, EntityAlias, _new_id, _now


@pytest.fixture
def db(tmp_path):
    """File-based DB with full schema."""
    d = DatabaseManager(str(tmp_path / "test.db"))
    d.initialize(schema)
    return d


def _make_entity(db, name, profile_id="default", scope="personal", entity_type="technology"):
    """Helper: create and store a canonical entity with self-alias."""
    entity = CanonicalEntity(
        entity_id=_new_id(),
        profile_id=profile_id,
        scope=scope,
        canonical_name=name,
        entity_type=entity_type,
        first_seen=_now(),
        last_seen=_now(),
        fact_count=3,
    )
    db.store_entity(entity)
    db.store_alias(EntityAlias(
        alias_id=_new_id(), entity_id=entity.entity_id,
        alias=name, confidence=1.0, source="canonical",
    ))
    return entity


def test_get_entities_by_scope(db):
    """get_entities_by_scope returns entities matching the given scope."""
    _make_entity(db, "React", scope="personal")
    _make_entity(db, "Docker", scope="global")
    _make_entity(db, "Python", scope="personal")

    results = db.get_entities_by_scope("default", scope="personal")
    assert len(results) == 2
    names = {e.canonical_name for e in results}
    assert names == {"React", "Python"}


def test_merge_entities_basic(db):
    """merge_entities merges source into target: rewrites facts, edges, aliases."""
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")

    db.store_alias(EntityAlias(
        alias_id=_new_id(), entity_id=source.entity_id,
        alias="React.js", confidence=0.9, source="fuzzy",
    ))

    result = db.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        profile_id="default",
    )

    assert result["aliases_moved"] == 2  # "ReactJS" + "React.js"
    assert result["source_deleted"] is True

    aliases = db.get_aliases_for_entity(target.entity_id)
    alias_texts = {a.alias for a in aliases}
    assert "ReactJS" in alias_texts
    assert "React.js" in alias_texts
    assert "React" in alias_texts  # original self-alias


def test_merge_entities_updates_facts(db):
    """merge_entities rewrites canonical_entities_json in atomic_facts."""
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")

    fact_id = _new_id()
    db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, profile_id, content, confidence, scope, "
        " entities_json, canonical_entities_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (fact_id, "default", "React uses JSX", 0.9, "personal",
         json.dumps(["ReactJS"]), json.dumps([source.entity_id])),
    )

    result = db.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        profile_id="default",
    )

    assert result["facts_updated"] == 1

    rows = db.execute(
        "SELECT canonical_entities_json FROM atomic_facts WHERE fact_id = ?",
        (fact_id,),
    )
    entities = json.loads(dict(rows[0])["canonical_entities_json"])
    assert target.entity_id in entities
    assert source.entity_id not in entities


def test_merge_entities_updates_graph_edges(db):
    """merge_entities rewrites graph_edges source/target IDs."""
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")
    other = _make_entity(db, "TypeScript", scope="global")

    edge_id = _new_id()
    db.execute(
        "INSERT INTO graph_edges "
        "(edge_id, profile_id, source_id, target_id, edge_type, weight, "
        " created_at, scope) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)",
        (edge_id, "default", source.entity_id, other.entity_id,
         "related_to", 0.8, "personal"),
    )

    result = db.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        profile_id="default",
    )

    assert result["edges_updated"] == 1

    rows = db.execute(
        "SELECT source_id FROM graph_edges WHERE edge_id = ?",
        (edge_id,),
    )
    assert dict(rows[0])["source_id"] == target.entity_id


def test_merge_entities_same_id_raises(db):
    """merge_entities raises if source and target are the same."""
    entity = _make_entity(db, "React", scope="global")
    with pytest.raises(ValueError, match="same entity"):
        db.merge_entities(
            source_entity_id=entity.entity_id,
            target_entity_id=entity.entity_id,
            profile_id="default",
        )


def test_merge_entities_target_not_found(db):
    """merge_entities raises if target entity does not exist."""
    source = _make_entity(db, "ReactJS", scope="personal")
    with pytest.raises(ValueError, match="Target entity"):
        db.merge_entities(
            source_entity_id=source.entity_id,
            target_entity_id="nonexistent_id",
            profile_id="default",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_merge.py -v`
Expected: FAIL — `AttributeError: 'DatabaseManager' object has no attribute 'get_entities_by_scope'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/superlocalmemory/storage/database.py` (after `get_aliases_for_entity` method, around line 643):

```python
    def get_entities_by_scope(
        self,
        profile_id: str,
        scope: str = "personal",
    ) -> list[CanonicalEntity]:
        """List all entities for a profile with the given scope."""
        rows = self.execute(
            "SELECT * FROM canonical_entities "
            "WHERE profile_id = ? AND scope = ? "
            "ORDER BY canonical_name COLLATE NOCASE",
            (profile_id, scope),
        )
        return [
            CanonicalEntity(
                entity_id=d["entity_id"],
                profile_id=d["profile_id"],
                scope=d.get("scope", "personal"),
                shared_with=json.loads(d["shared_with"]) if d.get("shared_with") else None,
                canonical_name=d["canonical_name"],
                entity_type=d["entity_type"],
                first_seen=d["first_seen"],
                last_seen=d["last_seen"],
                fact_count=d["fact_count"],
            )
            for r in rows
            for d in (dict(r),)
        ]

    def merge_entities(
        self,
        source_entity_id: str,
        target_entity_id: str,
        profile_id: str,
    ) -> dict[str, int | bool]:
        """Merge source entity into target entity (atomic via transaction).

        Moves aliases, rewrites atomic_facts + graph_edges + temporal_events
        + entity_profiles + memory_scenes, then deletes source entity.
        All operations run inside a single transaction — rollback on any error.

        Returns dict with counts of affected rows.
        """
        if source_entity_id == target_entity_id:
            raise ValueError("Cannot merge an entity into itself (same entity_id)")

        # Verify target exists (outside transaction — cheap read)
        target_rows = self.execute(
            "SELECT entity_id FROM canonical_entities WHERE entity_id = ?",
            (target_entity_id,),
        )
        if not target_rows:
            raise ValueError(f"Target entity {target_entity_id} not found")

        result: dict[str, int | bool] = {}

        with self.transaction():
            # 1. Move aliases from source to target
            alias_rows = self.execute(
                "SELECT alias_id, alias, confidence, source FROM entity_aliases "
                "WHERE entity_id = ?",
                (source_entity_id,),
            )
            aliases_moved = 0
            for r in alias_rows:
                d = dict(r)
                existing = self.execute(
                    "SELECT alias_id FROM entity_aliases "
                    "WHERE entity_id = ? AND LOWER(alias) = LOWER(?)",
                    (target_entity_id, d["alias"]),
                )
                if not existing:
                    self.execute(
                        "INSERT OR REPLACE INTO entity_aliases "
                        "(alias_id, entity_id, alias, confidence, source) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (_new_id(), target_entity_id, d["alias"], d["confidence"], d["source"]),
                    )
                    aliases_moved += 1
            self.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (source_entity_id,))
            result["aliases_moved"] = aliases_moved

            # 2. Rewrite atomic_facts: replace source_entity_id in
            #    canonical_entities_json arrays. Uses LIKE for initial filter
            #    (consistent with existing get_facts_by_entity), then exact
            #    list membership check in Python.
            facts = self.execute(
                "SELECT fact_id, canonical_entities_json FROM atomic_facts "
                "WHERE profile_id = ? AND canonical_entities_json LIKE ?",
                (profile_id, f'%"{source_entity_id}"%'),
            )
            facts_updated = 0
            for r in facts:
                d = dict(r)
                try:
                    entities = json.loads(d["canonical_entities_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if source_entity_id in entities:
                    entities = [
                        target_entity_id if eid == source_entity_id else eid
                        for eid in entities
                    ]
                    self.execute(
                        "UPDATE atomic_facts SET canonical_entities_json = ? "
                        "WHERE fact_id = ?",
                        (json.dumps(entities), d["fact_id"]),
                    )
                    facts_updated += 1
            result["facts_updated"] = facts_updated

            # 3. Rewrite graph_edges — use SELECT-then-UPDATE for reliable counting
            edges_updated = 0

            # Count + update: source_id references
            src_count = self.execute(
                "SELECT COUNT(*) AS c FROM graph_edges "
                "WHERE source_id = ? AND profile_id = ?",
                (source_entity_id, profile_id),
            )
            count_as_source = dict(src_count[0])["c"] if src_count else 0

            # Delete edges that would become self-loops BEFORE updating
            self.execute(
                "DELETE FROM graph_edges WHERE source_id = ? AND target_id = ? "
                "AND profile_id = ?",
                (source_entity_id, target_entity_id, profile_id),
            )

            self.execute(
                "UPDATE graph_edges SET source_id = ? "
                "WHERE source_id = ? AND profile_id = ?",
                (target_entity_id, source_entity_id, profile_id),
            )
            edges_updated += count_as_source

            # Count + update: target_id references
            tgt_count = self.execute(
                "SELECT COUNT(*) AS c FROM graph_edges "
                "WHERE target_id = ? AND profile_id = ?",
                (source_entity_id, profile_id),
            )
            count_as_target = dict(tgt_count[0])["c"] if tgt_count else 0

            self.execute(
                "DELETE FROM graph_edges WHERE target_id = ? AND source_id = ? "
                "AND profile_id = ?",
                (source_entity_id, target_entity_id, profile_id),
            )

            self.execute(
                "UPDATE graph_edges SET target_id = ? "
                "WHERE target_id = ? AND profile_id = ?",
                (target_entity_id, source_entity_id, profile_id),
            )
            edges_updated += count_as_target
            result["edges_updated"] = edges_updated

            # 4. Rewrite dependent tables with entity_id FK references
            #    (temporal_events, entity_profiles — no CASCADE, must repoint)
            self.execute(
                "UPDATE temporal_events SET entity_id = ? WHERE entity_id = ?",
                (target_entity_id, source_entity_id),
            )
            self.execute(
                "UPDATE entity_profiles SET entity_id = ? WHERE entity_id = ?",
                (target_entity_id, source_entity_id),
            )

            # 5. Update target fact_count
            source_entity = self.execute(
                "SELECT fact_count FROM canonical_entities WHERE entity_id = ?",
                (source_entity_id,),
            )
            source_fc = dict(source_entity[0])["fact_count"] if source_entity else 0
            self.execute(
                "UPDATE canonical_entities SET fact_count = fact_count + ? "
                "WHERE entity_id = ?",
                (source_fc, target_entity_id),
            )

            # 6. Delete source entity
            self.execute(
                "DELETE FROM canonical_entities WHERE entity_id = ?",
                (source_entity_id,),
            )
            result["source_deleted"] = True

        return result
```

Key design decisions:
- **Transaction wrapping**: All writes inside `with self.transaction()` — atomic rollback on error
- **SELECT-then-UPDATE**: Count matching rows before UPDATE since `execute()` doesn't expose `rowcount` (creates new connection per call)
- **Self-loop prevention**: Delete `source→target` and `target→source` edges BEFORE updating to avoid UNIQUE constraint violations
- **Dependent tables**: Rewrites `temporal_events` and `entity_profiles` entity_id references (FK has no CASCADE)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_merge.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/storage/database.py tests/test_entity_merge.py
git commit -m "feat(storage): add get_entities_by_scope() and merge_entities() for entity consolidation"
```

---

### Task 6: Remove Task — merged into Task 5

The original Task 6 (`_rows_affected` fix) is no longer needed — Task 5 now uses SELECT-then-UPDATE counting which doesn't require `_rows_affected()`.

---

## Chunk 3: Entity Merge — Engine + CLI + MCP (Tasks 7–10)

### Task 7: Add merge_entities facade to MemoryEngine

**Files:**
- Modify: `src/superlocalmemory/core/engine.py` (add `merge_entities()` method)
- Modify: `tests/test_entity_merge.py` (add engine-level test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entity_merge.py — append

def test_engine_merge_entities(tmp_path):
    """MemoryEngine.merge_entities delegates to DatabaseManager."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine
    from superlocalmemory.storage.models import CanonicalEntity, EntityAlias, _new_id, _now

    config = SLMConfig(base_dir=tmp_path)
    engine = MemoryEngine(config=config)
    engine.initialize()

    source = CanonicalEntity(
        entity_id=_new_id(), profile_id="default", scope="personal",
        canonical_name="ReactJS", entity_type="technology",
        first_seen=_now(), last_seen=_now(), fact_count=1,
    )
    target = CanonicalEntity(
        entity_id=_new_id(), profile_id="default", scope="global",
        canonical_name="React", entity_type="technology",
        first_seen=_now(), last_seen=_now(), fact_count=5,
    )
    engine._db.store_entity(source)
    engine._db.store_entity(target)
    engine._db.store_alias(EntityAlias(
        alias_id=_new_id(), entity_id=source.entity_id,
        alias="ReactJS", confidence=1.0, source="canonical",
    ))

    result = engine.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
    )

    assert result["source_deleted"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_merge.py::test_engine_merge_entities -v`
Expected: FAIL — `AttributeError: 'MemoryEngine' object has no attribute 'merge_entities'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/superlocalmemory/core/engine.py` (after the `store()` method):

```python
    def merge_entities(
        self,
        source_entity_id: str,
        target_entity_id: str,
    ) -> dict[str, int | bool]:
        """Merge source entity into target entity.

        Moves aliases, rewrites facts and edges, deletes source.
        Typically used to consolidate pre-Phase 3 personal entities
        into global authoritative entities.

        Args:
            source_entity_id: Entity to merge from (will be deleted).
            target_entity_id: Entity to merge into (kept).

        Returns:
            Dict with aliases_moved, facts_updated, edges_updated, source_deleted.
        """
        return self._db.merge_entities(
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            profile_id=self._profile_id,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_merge.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/superlocalmemory/core/engine.py tests/test_entity_merge.py
git commit -m "feat(engine): add merge_entities() facade for entity consolidation"
```

---

### Task 8: Add `slm entity merge` + `slm entity list` CLI commands

**Files:**
- Modify: `src/superlocalmemory/cli/commands.py` (add `_cmd_entity_dispatch`, `cmd_entity_merge`, `cmd_entity_list`)
- Modify: `src/superlocalmemory/cli/main.py` (register `entity` subparser)
- Modify: `tests/test_entity_merge.py` (add CLI tests)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entity_merge.py — append

def test_cmd_entity_merge(tmp_path, monkeypatch, capsys):
    """cmd_entity_merge constructs engine and calls merge_entities."""
    from superlocalmemory.cli.commands import cmd_entity_merge
    from unittest.mock import MagicMock, patch
    from argparse import Namespace

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))

    args = Namespace(source="src_123", target="tgt_456", profile="default", json=False)

    with patch("superlocalmemory.cli.commands.SLMConfig") as MockConfig, \
         patch("superlocalmemory.cli.commands.MemoryEngine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.merge_entities.return_value = {
            "aliases_moved": 2, "facts_updated": 1, "edges_updated": 0,
            "source_deleted": True,
        }
        MockEngine.return_value = mock_engine
        MockConfig.load.return_value = MagicMock()

        cmd_entity_merge(args)

    mock_engine.merge_entities.assert_called_once_with(
        source_entity_id="src_123", target_entity_id="tgt_456",
    )
    captured = capsys.readouterr()
    assert "Merged" in captured.out


def test_cmd_entity_list(tmp_path, monkeypatch, capsys):
    """cmd_entity_list shows entities with their scope."""
    from superlocalmemory.cli.commands import cmd_entity_list
    from unittest.mock import MagicMock, patch
    from argparse import Namespace
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import CanonicalEntity, _new_id, _now

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))

    args = Namespace(scope="personal", profile="default", json=False, limit=50)

    with patch("superlocalmemory.cli.commands.SLMConfig") as MockConfig, \
         patch("superlocalmemory.cli.commands.MemoryEngine") as MockEngine:
        mock_engine = MagicMock()

        # Use a real DB for entity listing
        db = DatabaseManager(str(tmp_path / "test.db"))
        db.initialize(schema)
        db.store_entity(CanonicalEntity(
            entity_id=_new_id(), profile_id="default", scope="personal",
            canonical_name="ReactJS", entity_type="technology",
            first_seen=_now(), last_seen=_now(), fact_count=3,
        ))
        mock_engine._db = db
        MockEngine.return_value = mock_engine
        MockConfig.load.return_value = MagicMock()

        cmd_entity_list(args)

    captured = capsys.readouterr()
    assert "ReactJS" in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_merge.py::test_cmd_entity_merge -v`
Expected: FAIL — `ImportError: cannot import name 'cmd_entity_merge'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/superlocalmemory/cli/commands.py` (after the dispatch function, around line 136). Follow the `_cmd_db_dispatch` pattern from line 23:

```python
def _cmd_entity_dispatch(args: Namespace) -> None:
    """Route ``slm entity ...`` subcommands."""
    sub = getattr(args, "entity_command", None)
    if sub == "merge":
        cmd_entity_merge(args)
        return
    if sub == "list":
        cmd_entity_list(args)
        return
    print("Usage: slm entity <merge|list> [options]")
    sys.exit(2)


def cmd_entity_merge(args: Namespace) -> None:
    """Merge source entity into target entity."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine

    config = SLMConfig.load()
    engine = MemoryEngine(config=config)
    engine.initialize()

    result = engine.merge_entities(
        source_entity_id=args.source,
        target_entity_id=args.target,
    )

    if getattr(args, "json", False):
        from superlocalmemory.cli.json_output import json_print
        json_print("entity-merge", data=result)
        return

    print(f"Merged entity {args.source} -> {args.target}")
    print(f"  Aliases moved: {result.get('aliases_moved', 0)}")
    print(f"  Facts updated: {result.get('facts_updated', 0)}")
    print(f"  Edges updated: {result.get('edges_updated', 0)}")
    if result.get("source_deleted"):
        print("  Source entity deleted")
    else:
        print("  WARNING: Source entity was NOT deleted")


def cmd_entity_list(args: Namespace) -> None:
    """List entities filtered by scope."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine

    config = SLMConfig.load()
    engine = MemoryEngine(config=config)
    engine.initialize()

    scope = getattr(args, "scope", "personal")
    limit = getattr(args, "limit", 50)

    entities = engine._db.get_entities_by_scope(
        profile_id=getattr(args, "profile", "default"),
        scope=scope,
    )[:limit]

    if getattr(args, "json", False):
        from superlocalmemory.cli.json_output import json_print
        json_print("entity-list", data=[{
            "entity_id": e.entity_id,
            "canonical_name": e.canonical_name,
            "scope": e.scope,
            "entity_type": e.entity_type,
            "fact_count": e.fact_count,
        } for e in entities])
        return

    if not entities:
        print(f"No entities found with scope='{scope}'")
        return

    print(f"Entities (scope={scope}):")
    for e in entities:
        print(f"  {e.entity_id[:12]}...  {e.canonical_name:30s}  "
              f"type={e.entity_type:15s}  facts={e.fact_count}")
```

Register `"entity"` in the handlers dict in `dispatch()` (around line 121):

```python
        "entity": _cmd_entity_dispatch,
```

Register the subparser in `src/superlocalmemory/cli/main.py`. Find where subparsers are defined and add:

```python
    # Entity management
    entity_sp = subparsers.add_parser("entity", help="Entity management commands")
    entity_sub = entity_sp.add_subparsers(dest="entity_command")

    merge_p = entity_sub.add_parser("merge", help="Merge source entity into target")
    merge_p.add_argument("source", help="Source entity ID (will be deleted)")
    merge_p.add_argument("target", help="Target entity ID (kept)")
    merge_p.add_argument("--profile", default="default", help="Profile ID")
    merge_p.add_argument("--json", action="store_true")

    list_p = entity_sub.add_parser("list", help="List entities by scope")
    list_p.add_argument("--scope", default="personal",
                        choices=["personal", "global", "shared"])
    list_p.add_argument("--profile", default="default")
    list_p.add_argument("--limit", type=int, default=50)
    list_p.add_argument("--json", action="store_true")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_merge.py::test_cmd_entity_merge tests/test_entity_merge.py::test_cmd_entity_list -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -q --tb=short -x`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/cli/commands.py src/superlocalmemory/cli/main.py tests/test_entity_merge.py
git commit -m "feat(cli): add 'slm entity merge' and 'slm entity list' commands"
```

---

### Task 9: Add merge_entities MCP tool

**Files:**
- Modify: `src/superlocalmemory/mcp/tools_core.py` (add tool in `register_core_tools()`)
- Modify: `src/superlocalmemory/mcp/server.py` (add to `_ESSENTIAL_TOOLS`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entity_merge.py — append

def test_mcp_merge_entities_registered():
    """merge_entities tool is registered in MCP server module."""
    from importlib import import_module
    import inspect

    tools_core = import_module("superlocalmemory.mcp.tools_core")

    # Find the merge_entities function in the module
    assert hasattr(tools_core, "_tool_merge_entities"), \
        "tools_core should define _tool_merge_entities (registered via server.tool())"

    sig = inspect.signature(tools_core._tool_merge_entities)
    params = set(sig.parameters.keys())
    assert "source_entity_id" in params
    assert "target_entity_id" in params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_merge.py::test_mcp_merge_entities_registered -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

In `src/superlocalmemory/mcp/tools_core.py`, add inside `register_core_tools()` function (at the end, before the closing of the function). Follow the existing pattern where tools are defined as nested functions and registered via `@server.tool()`:

```python
    @server.tool()
    async def merge_entities(
        source_entity_id: str,
        target_entity_id: str,
        profile_id: str = "",
    ) -> dict:
        """Merge source entity into target entity. Consolidates duplicate entities.

        Moves all aliases, rewrites facts and graph edges, deletes source entity.
        Use to merge pre-Phase 3 personal entities into global entities.

        Args:
            source_entity_id: Entity ID to merge from (will be deleted).
            target_entity_id: Entity ID to merge into (kept).
            profile_id: Profile ID (optional, uses default).
        """
        try:
            engine = get_engine()
            pid = profile_id or engine.profile_id
            result = engine.merge_entities(
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
            )
            return {
                "success": True,
                "data": result,
                "message": (
                    f"Merged {source_entity_id} -> {target_entity_id}: "
                    f"{result.get('aliases_moved', 0)} aliases, "
                    f"{result.get('facts_updated', 0)} facts, "
                    f"{result.get('edges_updated', 0)} edges"
                ),
            }
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Merge failed: {e}"}
```

In `src/superlocalmemory/mcp/server.py`, add to `_ESSENTIAL_TOOLS` set (around line 100):

```python
    # Multi-scope memory: entity management
    "merge_entities",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_merge.py::test_mcp_merge_entities_registered -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -q --tb=short -x`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add src/superlocalmemory/mcp/tools_core.py src/superlocalmemory/mcp/server.py tests/test_entity_merge.py
git commit -m "feat(mcp): add merge_entities tool for entity consolidation via MCP"
```

---

### Task 10: End-to-end smoke test

**Files:**
- Modify: `tests/test_entity_merge.py` (add E2E test)

- [ ] **Step 1: Write E2E test**

```python
# tests/test_entity_merge.py — append

def test_e2e_merge_workflow(tmp_path, monkeypatch):
    """Full workflow: create entities, store facts, merge, verify."""
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))

    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine
    from superlocalmemory.storage.models import CanonicalEntity, _new_id, _now
    import json

    config = SLMConfig(base_dir=tmp_path)
    engine = MemoryEngine(config=config)
    engine.initialize()

    # Create personal entity "ReactJS"
    source = CanonicalEntity(
        entity_id=_new_id(), profile_id="default", scope="personal",
        canonical_name="ReactJS", entity_type="technology",
        first_seen=_now(), last_seen=_now(), fact_count=0,
    )
    engine._db.store_entity(source)

    # Create global entity "React"
    target = CanonicalEntity(
        entity_id=_new_id(), profile_id="default", scope="global",
        canonical_name="React", entity_type="technology",
        first_seen=_now(), last_seen=_now(), fact_count=0,
    )
    engine._db.store_entity(target)

    # Store a fact referencing the personal entity
    fact_id = _new_id()
    engine._db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, profile_id, content, confidence, scope, "
        " entities_json, canonical_entities_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (fact_id, "default", "ReactJS supports hooks", 0.9, "personal",
         json.dumps(["ReactJS"]), json.dumps([source.entity_id])),
    )

    # Create edge: personal "ReactJS" -> global "TypeScript"
    ts_entity = CanonicalEntity(
        entity_id=_new_id(), profile_id="default", scope="global",
        canonical_name="TypeScript", entity_type="technology",
        first_seen=_now(), last_seen=_now(), fact_count=0,
    )
    engine._db.store_entity(ts_entity)
    engine._db.execute(
        "INSERT INTO graph_edges "
        "(edge_id, profile_id, source_id, target_id, edge_type, weight, "
        " created_at, scope) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)",
        (_new_id(), "default", source.entity_id, ts_entity.entity_id,
         "related_to", 0.8, "personal"),
    )

    # Merge personal "ReactJS" -> global "React"
    result = engine.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
    )

    # Verify
    assert result["source_deleted"] is True
    assert result["facts_updated"] == 1
    assert result["edges_updated"] == 1

    # Fact now references global "React"
    rows = engine._db.execute(
        "SELECT canonical_entities_json FROM atomic_facts WHERE fact_id = ?",
        (fact_id,),
    )
    entities = json.loads(dict(rows[0])["canonical_entities_json"])
    assert target.entity_id in entities
    assert source.entity_id not in entities

    # Edge: global "React" -> global "TypeScript"
    edges = engine._db.execute(
        "SELECT source_id, target_id FROM graph_edges WHERE profile_id = ?",
        ("default",),
    )
    edge_list = [dict(r) for r in edges]
    assert any(e["source_id"] == target.entity_id for e in edge_list)
    assert not any(e["source_id"] == source.entity_id for e in edge_list)

    # Source entity gone
    assert not engine._db.execute(
        "SELECT entity_id FROM canonical_entities WHERE entity_id = ?",
        (source.entity_id,),
    )

    # Aliases moved: "ReactJS" points to global "React"
    aliases = engine._db.get_aliases_for_entity(target.entity_id)
    alias_texts = {a.alias for a in aliases}
    assert "ReactJS" in alias_texts
```

- [ ] **Step 2: Run the E2E test**

Run: `pytest tests/test_entity_merge.py::test_e2e_merge_workflow -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -q --tb=short -x`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add tests/test_entity_merge.py
git commit -m "test: add E2E smoke test for entity merge workflow"
```
