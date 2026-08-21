# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Mode A has to work with the network gone. Not degrade — work.

Mode A is the floor, and the floor is what the product promises when there is no
model, no key and no connection. That promise is either kept under those exact
conditions or it is not a promise, and the fallback code being present is not
evidence that it runs.

So every test here severs the network first, at ``socket.socket.connect``, which
catches localhost too. A model server on 11434 is as unreachable as one across
the internet, and it should be: a user on a plane has neither.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.storage.models import Mode


@pytest.fixture(autouse=True)
def severed_network(monkeypatch):
    """No outbound TCP, including to localhost.

    Patched at the socket layer rather than at each client, so a subsystem that
    reaches the network through a library nobody remembered still fails here.
    """
    attempts: list[object] = []
    real_connect = socket.socket.connect

    def refuse(self, address):  # noqa: ANN001
        attempts.append(address)
        raise ConnectionRefusedError(f"network severed by test: {address}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(
        socket.socket, "connect_ex",
        lambda self, address: (attempts.append(address), 111)[1],
    )
    yield attempts
    socket.socket.connect = real_connect


@pytest.fixture
def mode_a_engine(tmp_path: Path, monkeypatch):
    """Mode A with no model available at all.

    The promise is "no network, no key, NO MODEL", and the last clause is the
    one worth testing: with an embedder the answer leans on meaning, and the
    interesting question is what happens when it cannot.

    The embedder is forced absent rather than left to fail on its own, for two
    reasons. It makes the test deterministic — otherwise the result depends on
    whether a 2 GB model happens to be cached on the machine running it. And it
    makes the test fast: loading that model takes minutes by this codebase's own
    account, which is why an earlier version of this file timed out and looked
    like a hang in Mode A rather than a slow model load.
    """
    from superlocalmemory.core.engine import MemoryEngine

    monkeypatch.setattr(
        "superlocalmemory.core.engine_wiring.init_embedder",
        lambda config: None,
    )
    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.retrieval.use_cross_encoder = False
    engine = MemoryEngine(config)
    engine.initialize()
    assert engine._embedder is None, "the fixture did not remove the embedder"
    yield engine
    engine.close()


_FACTS = [
    "The invoice run happens on the last working day of the month.",
    "Frankfurt hosts the build runner.",
    "The staging database restores from a nightly snapshot.",
]


# ---------------------------------------------------------------------------
# Storing and recalling
# ---------------------------------------------------------------------------

def test_a_memory_can_be_stored_with_no_network(mode_a_engine):
    fact_ids = mode_a_engine.store("The invoice run is on the last working day.")
    assert fact_ids, "Mode A could not store a memory without a network"


def test_a_memory_can_be_found_with_no_network(mode_a_engine):
    for text in _FACTS:
        mode_a_engine.store(text)
    response = mode_a_engine.recall("invoice run")
    assert response is not None
    assert not getattr(response, "abstained", False) or response.results == []
    # The claim is that recall RETURNS, not that it finds something: an empty
    # answer from an empty store is correct. An exception is not.
    assert isinstance(response.results, list)


def test_recall_finds_the_right_memory_with_no_network(mode_a_engine):
    """Lexical retrieval alone must still answer a keyword question.

    This is the floor's actual job. Without it, Mode A "works" only in the sense
    that it does not raise.
    """
    for text in _FACTS:
        mode_a_engine.store(text)
    response = mode_a_engine.recall("Frankfurt build runner")
    joined = " ".join(r.fact.content for r in response.results)
    assert "Frankfurt" in joined, (
        f"Mode A could not retrieve on an exact keyword; got {joined[:200]!r}"
    )


def test_no_subsystem_reached_for_the_network_during_a_recall(
    mode_a_engine, severed_network,
):
    """Not just "it survived the network being down" — it never tried.

    A subsystem that attempts a connection and swallows the refusal still costs
    the user a timeout on every call, which on a plane is the difference between
    slow and unusable.
    """
    for text in _FACTS:
        mode_a_engine.store(text)
    severed_network.clear()
    mode_a_engine.recall("invoice run")
    assert not severed_network, (
        f"Mode A recall attempted {len(severed_network)} outbound "
        f"connection(s): {severed_network[:5]}"
    )


# ---------------------------------------------------------------------------
# The summary writers, which are the pattern the rest is measured against
# ---------------------------------------------------------------------------

def test_session_summary_falls_back_to_extractive(mode_a_engine):
    from superlocalmemory.summaries.session_summary import (
        generate_session_summary,
    )

    facts = [type("F", (), {"content": t, "fact_id": f"f{i}"})()
             for i, t in enumerate(_FACTS)]
    result = generate_session_summary("s1", facts, len(facts), config=None)
    assert result is not None, "Mode A summary returned nothing"


def test_close_session_works_with_no_network(mode_a_engine):
    mode_a_engine.store("A thing worth remembering.", session_id="s-offline")
    # Must not raise: closing a session summarises it, and summarising is
    # exactly where an unguarded model call would hide.
    mode_a_engine.close_session("s-offline")
