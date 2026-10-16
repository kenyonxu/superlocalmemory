"""PR-247/3: protection arriving after planning wins at the write boundary."""

from types import SimpleNamespace

import pytest

from superlocalmemory.mcp.tools_v28 import register_v28_tools
from tests.mcp.test_loop_tools import _Capture
from tests.test_learning.test_forgetting_protections import PID, age, scheduler
from tests.test_learning.test_forgetting_scheduler import db_with_facts as db_with_facts


def protect(db, kind):
    if kind == "pin":
        db.execute("UPDATE atomic_facts SET pinned=1 WHERE fact_id='fact_009'")
    elif kind == "core":
        db.execute(
            "INSERT INTO core_memory_blocks (block_id,profile_id,block_type,content,"
            "source_fact_ids) VALUES ('core',?,'custom','kept','[\"fact_009\"]')",
            (PID,),
        )
    else:
        db.execute("ALTER TABLE action_outcomes ADD COLUMN reward REAL")
        db.execute("ALTER TABLE action_outcomes ADD COLUMN settled_at TEXT")
        db.execute(
            "INSERT INTO action_outcomes (outcome_id,profile_id,fact_ids_json,reward,settled_at) "
            "VALUES ('late',?,'[\"fact_009\"]',.9,datetime('now'))", (PID,),
        )


def assert_kept(db):
    assert db.get_fact("fact_009").lifecycle.value == "active"
    assert db.get_retention("fact_009", PID)["lifecycle_zone"] == "active"


def seed_retention(db):
    db.upsert_retention("fact_009", PID, 1.0, 1.0, 0, "2000-01-01", "active")


@pytest.mark.parametrize("kind", ["pin", "core", "reward"])
def test_decay_protection_added_at_batch_entry(db_with_facts, monkeypatch, kind):
    db = db_with_facts
    age(db)
    seed_retention(db)
    original = db.batch_upsert_retention

    def interleave(results, profile):
        protect(db, kind)
        return original(results, profile)

    monkeypatch.setattr(db, "batch_upsert_retention", interleave)
    scheduler(db).run_decay_cycle(PID, force=True)
    assert_kept(db)


@pytest.mark.parametrize("kind", ["pin", "core", "reward"])
def test_soft_delete_rechecks_current_protection(db_with_facts, kind):
    db = db_with_facts
    seed_retention(db)
    protect(db, kind)
    db.soft_delete_fact("fact_009", PID)
    assert_kept(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pin", "core", "reward"])
@pytest.mark.parametrize("surface", ["mcp", "http"])
async def test_compaction_protection_added_after_planning(
    db_with_facts, monkeypatch, kind, surface,
):
    db = db_with_facts
    age(db)
    seed_retention(db)
    hooks = SimpleNamespace(
        run_pre=lambda *_: protect(db, kind), run_post=lambda *_: None,
    )
    engine = SimpleNamespace(_db=db, profile_id=PID, _hooks=hooks)
    if surface == "mcp":
        server = _Capture()
        register_v28_tools(server, lambda: engine)
        result = await server.fns["compact_memories"](dry_run=False)
    else:
        from superlocalmemory.server.routes import lifecycle

        monkeypatch.setattr(lifecycle, "get_engine_lazy", lambda _: engine)
        monkeypatch.setattr(lifecycle, "get_active_profile", lambda: PID)

        def authorized(*_, **kwargs):
            protect(db, kind)
            return SimpleNamespace(complete=lambda: None)

        monkeypatch.setattr(lifecycle, "authorize_route_mutation", authorized)
        request = SimpleNamespace(app=SimpleNamespace(state=None))
        result = await lifecycle.trigger_compaction(request, {"dry_run": False})
    assert result["success"], result
    assert_kept(db)
