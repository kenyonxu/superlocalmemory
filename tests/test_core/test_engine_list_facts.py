# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""``engine.list_facts``: profile-threaded, newest-first, LIMIT push-down.

Spec §2/§5: the per-request ``profile_id`` threading convention established
on the write paths (``store`` / ``store_fact_direct`` / ``store_fast``)
applies to the list entry as well:

  * ``profile_id="b"`` — only profile ``b``'s facts come back;
  * ``profile_id=None`` (and ``""``) — the engine's active profile is
    listed, byte-for-byte the pre-feature behaviour;
  * ``limit`` is pushed down to ``db.get_all_facts`` as a SQL LIMIT, not
    applied by slicing in Python;
  * ``engine._profile_id`` is NEVER mutated by a list call.

Fixture convention: ``engine_with_mock_deps`` from ``tests/conftest.py``
(real SQLite DB on tmp_path, real schema, mocked embedder, no LLM) — the
same convention ``test_engine_store_profile.py`` uses. Assertions are made
against the real database rows, not internal mocks.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

from superlocalmemory.core.config import CANONICAL_LIST_LIMIT
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.storage.models import AtomicFact, FactType

TARGET = "profile-target"


def _seed_profile(engine: MemoryEngine, name: str = TARGET) -> None:
    """FK on atomic_facts → profiles; seed the target row (same INSERT OR
    IGNORE statement schema.create_all_tables uses for 'default')."""
    engine._db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
        (name, name),
    )


def _store(engine: MemoryEngine, fact_id: str, content: str,
           profile_id: str | None = None) -> str:
    fact = AtomicFact(
        fact_id=fact_id, memory_id="", content=content,
        fact_type=FactType.SEMANTIC, entities=["Probe"], confidence=0.9,
    )
    return engine.store_fact_direct(fact, profile_id=profile_id)


# ---------------------------------------------------------------------------
# Signature contract — Tasks 2/3 consume this exact shape
# ---------------------------------------------------------------------------

class TestSignatureContract:
    def test_profile_id_is_keyword_only_last_and_defaults_none(self) -> None:
        params = inspect.signature(MemoryEngine.list_facts).parameters
        assert "profile_id" in params, "list_facts lost profile_id"
        param = params["profile_id"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None
        assert param is list(params.values())[-1], (
            "profile_id must be appended last so existing positional "
            "callers cannot shift"
        )

    def test_limit_defaults_to_canonical_list_limit(self) -> None:
        params = inspect.signature(MemoryEngine.list_facts).parameters
        assert params["limit"].default == CANONICAL_LIST_LIMIT


# ---------------------------------------------------------------------------
# Routing behaviour
# ---------------------------------------------------------------------------

class TestListFacts:
    def test_explicit_profile_routes(self, engine_with_mock_deps) -> None:
        eng = engine_with_mock_deps
        _seed_profile(eng)
        _store(eng, "lf-a-1", "Probe fact in the active profile")
        _store(eng, "lf-b-1", "Probe fact routed to the target", profile_id=TARGET)
        _store(eng, "lf-b-2", "Second probe fact for the target", profile_id=TARGET)

        facts = eng.list_facts(limit=5, profile_id=TARGET)

        ids = {f.fact_id for f in facts}
        assert ids == {"lf-b-1", "lf-b-2"}, (
            f"explicit profile must list only the target's facts, got {ids!r}"
        )

    def test_none_falls_back_to_active(self, engine_with_mock_deps) -> None:
        eng = engine_with_mock_deps
        _seed_profile(eng)
        _store(eng, "lf-act-1", "Probe fact with no profile named")
        _store(eng, "lf-tgt-1", "Probe fact in another profile", profile_id=TARGET)

        facts = eng.list_facts(limit=5)

        ids = {f.fact_id for f in facts}
        assert "lf-act-1" in ids
        assert "lf-tgt-1" not in ids, (
            "profile_id=None must list the active profile only"
        )

    def test_empty_string_falls_back_to_active(self, engine_with_mock_deps) -> None:
        eng = engine_with_mock_deps
        _seed_profile(eng)
        _store(eng, "lf-empty-1", "Probe fact beside an empty profile id")
        _store(eng, "lf-empty-tgt", "Probe fact in the target", profile_id=TARGET)

        facts = eng.list_facts(limit=5, profile_id="")

        ids = {f.fact_id for f in facts}
        assert "lf-empty-1" in ids
        assert "lf-empty-tgt" not in ids

    def test_limit_pushed_down(self, engine_with_mock_deps) -> None:
        eng = engine_with_mock_deps
        for i in range(5):
            _store(eng, f"lf-lim-{i}", f"Probe fact number {i}")

        with patch.object(
            eng._db, "get_all_facts", wraps=eng._db.get_all_facts,
        ) as spy:
            facts = eng.list_facts(limit=3)

        assert spy.call_args.kwargs.get("limit") == 3, (
            f"limit must reach get_all_facts as a kwarg, got {spy.call_args!r}"
        )
        assert len(facts) == 3, (
            "the SQL LIMIT must bound the result, not a Python-side slice "
            f"over a full fetch (got {len(facts)} facts)"
        )

    def test_active_pointer_untouched(self, engine_with_mock_deps) -> None:
        eng = engine_with_mock_deps
        _seed_profile(eng)
        before = eng._profile_id
        eng.list_facts(limit=5, profile_id=TARGET)
        assert eng._profile_id == before
