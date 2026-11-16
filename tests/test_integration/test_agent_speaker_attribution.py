"""Issue #265: an agent's own first person must not become "the user".

An agent (Ava) saves "I prefer short answers" through MCP. Before 4.2.7 the
write carried role="user" with no speaker, so the derived facts said
"the user prefers short answers" -- a preference the human never stated.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from superlocalmemory.core.config import RetrievalConfig, SLMConfig
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.encoding.speaker_spans import with_actor
from superlocalmemory.storage.models import Mode
from tests.test_integration.test_encoding_wiring import _MockEmbedder


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[MemoryEngine]:
    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.db_path = tmp_path / "test.db"
    config.retrieval = RetrievalConfig(use_cross_encoder=False)
    eng = MemoryEngine(config)
    with patch("superlocalmemory.core.engine_wiring.init_embedder",
               return_value=_MockEmbedder(768)):
        eng.initialize()
    from tests.conftest import force_sync_enrichment
    try:
        yield force_sync_enrichment(eng)
    finally:
        eng.close()


def _facts(engine: MemoryEngine) -> list[str]:
    return [r["content"] for r in engine._db.execute("SELECT content FROM atomic_facts")]


def test_agent_speaker_owns_its_first_person(engine: MemoryEngine) -> None:
    engine.store("I prefer short answers and I am working on the billing module.",
                 speaker="Ava", role="assistant")
    text = " ".join(_facts(engine)).lower()
    assert "the user" not in text, text
    assert "ava" in text, text


def test_user_first_person_still_means_the_user(engine: MemoryEngine) -> None:
    engine.store("I prefer dark mode in every editor.")
    text = " ".join(_facts(engine)).lower()
    assert "the user" in text, text


def test_with_actor_uses_role_when_no_speaker() -> None:
    assert with_actor("I shipped it.", "", "assistant") == "[Assistant]: I shipped it."
    assert with_actor("I shipped it.", "Ava", "assistant") == "[Ava]: I shipped it."


def test_daemon_resolves_agent_speaker() -> None:
    from superlocalmemory.core.agent_attribution import write_speaker

    # An explicit speaker always wins.
    assert write_speaker("Ava", {"agent_id": "claude-code"}) == ("Ava", "assistant")
    # A named agent connection writes as itself.
    assert write_speaker("", {"agent_id": "claude-code"}) == ("claude-code", "assistant")
    # The unnamed MCP sentinel is still an agent, not the user.
    assert write_speaker("", {"agent_id": "mcp_client"}) == ("", "assistant")
    # No agent (CLI, dashboard, Python API): the person typing is the user.
    assert write_speaker("", {}) == ("", "user")
    # "user"/"me" as an explicit speaker means the human.
    assert write_speaker("user", {"agent_id": "claude-code"}) == ("", "user")
