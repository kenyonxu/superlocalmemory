"""Tests for entity merge functionality."""

import json
import pytest
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import CanonicalEntity, EntityAlias, _new_id, _now


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(str(tmp_path / "test.db"))
    d.initialize(schema)
    return d


def _make_entity(db, name, profile_id="default", scope="personal", entity_type="technology"):
    entity = CanonicalEntity(
        entity_id=_new_id(), profile_id=profile_id, scope=scope,
        canonical_name=name, entity_type=entity_type,
        first_seen=_now(), last_seen=_now(), fact_count=3,
    )
    db.store_entity(entity)
    db.store_alias(EntityAlias(
        alias_id=_new_id(), entity_id=entity.entity_id,
        alias=name, confidence=1.0, source="canonical",
    ))
    return entity


def test_get_entities_by_scope(db):
    _make_entity(db, "React", scope="personal")
    _make_entity(db, "Docker", scope="global")
    _make_entity(db, "Python", scope="personal")
    results = db.get_entities_by_scope("default", scope="personal")
    assert len(results) == 2
    names = {e.canonical_name for e in results}
    assert names == {"React", "Python"}


def test_merge_entities_basic(db):
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
    assert "React" in alias_texts


def test_merge_entities_updates_facts(db):
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")
    memory_id = _new_id()
    db.execute(
        "INSERT INTO memories "
        "(memory_id, profile_id, content, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (memory_id, "default", "React uses JSX"),
    )
    fact_id = _new_id()
    db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, confidence, scope, "
        " entities_json, canonical_entities_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (fact_id, memory_id, "default", "React uses JSX", 0.9, "personal",
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
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")
    other = _make_entity(db, "TypeScript", scope="global")
    edge_id = _new_id()
    db.execute(
        "INSERT INTO graph_edges "
        "(edge_id, profile_id, source_id, target_id, edge_type, weight, "
        " created_at, scope) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)",
        (edge_id, "default", source.entity_id, other.entity_id,
         "entity", 0.8, "personal"),
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
    entity = _make_entity(db, "React", scope="global")
    with pytest.raises(ValueError, match="same entity"):
        db.merge_entities(
            source_entity_id=entity.entity_id,
            target_entity_id=entity.entity_id,
            profile_id="default",
        )


def test_merge_entities_target_not_found(db):
    source = _make_entity(db, "ReactJS", scope="personal")
    with pytest.raises(ValueError, match="Target entity"):
        db.merge_entities(
            source_entity_id=source.entity_id,
            target_entity_id="nonexistent_id",
            profile_id="default",
        )


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


def test_cmd_entity_merge(tmp_path, monkeypatch, capsys):
    """cmd_entity_merge constructs engine and calls merge_entities."""
    from superlocalmemory.cli.commands import cmd_entity_merge
    from unittest.mock import MagicMock, patch
    from argparse import Namespace

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))

    args = Namespace(source="src_123", target="tgt_456", profile="default", json=False)

    with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
         patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
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

    with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
         patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
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


def test_mcp_merge_entities_registered():
    """merge_entities tool is registered in MCP server module."""
    from importlib import import_module
    import inspect
    from unittest.mock import MagicMock

    tools_core = import_module("superlocalmemory.mcp.tools_core")

    # Trigger registration so the module-level ref is populated.
    # server.tool() must act as an identity decorator so the real function
    # (not a MagicMock wrapper) is stored in _tool_merge_entities.
    mock_server = MagicMock()
    mock_server.tool.side_effect = lambda **_kw: (lambda fn: fn)
    mock_get_engine = MagicMock()
    tools_core.register_core_tools(mock_server, mock_get_engine)

    # Find the merge_entities function in the module
    assert hasattr(tools_core, "_tool_merge_entities"), \
        "tools_core should define _tool_merge_entities (registered via server.tool())"

    sig = inspect.signature(tools_core._tool_merge_entities)
    params = set(sig.parameters.keys())
    assert "source_entity_id" in params
    assert "target_entity_id" in params
