"""BRN-15: persisted prompts must survive feedback and review on SQLite rows."""

import sqlite3
from datetime import UTC, datetime, timedelta

from superlocalmemory.core.config import ParameterizationConfig
from superlocalmemory.parameterization.prompt_injector import PromptInjector
from superlocalmemory.parameterization.prompt_lifecycle import PromptLifecycleManager
from superlocalmemory.parameterization.soft_prompt_generator import (
    SoftPromptGenerator,
    SoftPromptTemplate,
)
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.schema_v32 import V32_DDL


def test_persist_feedback_inject_review(tmp_path):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    with sqlite3.connect(str(tmp_path / "memory.db")) as conn:
        for ddl in V32_DDL:
            conn.executescript(ddl)
    cfg = ParameterizationConfig(enabled=True)
    injector = PromptInjector(db, SoftPromptGenerator(cfg), cfg)
    template = SoftPromptTemplate(
        "p1", "default", "identity", "The user is an architect.", [], 0.8, 0.5, 8, 1.0, True, 1
    )
    assert injector.store_prompts([template]) == 1
    lifecycle = PromptLifecycleManager(db, cfg)
    score = lifecycle.update_effectiveness("default", "identity", {"followed": 1.0})
    assert 0 < score < 1
    assert db.execute("SELECT effectiveness FROM soft_prompt_templates")[0][0] == score
    assert "architect" in injector.get_injection_context("default")
    assert lifecycle.run_lifecycle_review("default")["reviewed"] == 1
    db.close()


def test_review_does_not_reset_decay_clock(tmp_path):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    with sqlite3.connect(str(tmp_path / "memory.db")) as conn:
        for ddl in V32_DDL:
            conn.executescript(ddl)
    old = (datetime.now(UTC) - timedelta(hours=50)).isoformat()
    db.execute(
        "INSERT INTO soft_prompt_templates (prompt_id,profile_id,category,content,updated_at) "
        "VALUES ('p1','default','identity','Architect',?)",
        (old,),
    )
    lifecycle = PromptLifecycleManager(db, ParameterizationConfig())
    lifecycle.run_lifecycle_review("default")
    assert db.execute("SELECT updated_at FROM soft_prompt_templates")[0][0] == old
    assert lifecycle.compute_prompt_retention("p1") < 0.4
    db.close()
