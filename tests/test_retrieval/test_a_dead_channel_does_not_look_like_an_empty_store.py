# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Absence and failure must not look the same in an answer.

A channel that raises on every query and a channel that correctly found nothing
both used to come back as ``None`` with no entry in the candidate map. From
outside, a partly-broken retrieval path was indistinguishable from a store with
nothing relevant in it — and the remedies for those two are not the same.

Every test here forces a real fault through the real engine. Asserting that a
field exists proves nothing; asserting it carries the right value when a channel
is actually broken is the point.
"""

from __future__ import annotations

import time

import pytest

from superlocalmemory.retrieval import channel_status as chstat
from superlocalmemory.retrieval.engine import RetrievalEngine


class _Channel:
    """A channel that does exactly one thing, on demand."""

    def __init__(self, behaviour: str, hits=None, delay: float = 0.0) -> None:
        self._behaviour = behaviour
        self._hits = hits or []
        self._delay = delay
        self.calls = 0

    def search(self, *args, **kwargs):
        self.calls += 1
        if self._delay:
            time.sleep(self._delay)
        if self._behaviour == "raise":
            raise RuntimeError("this channel is broken")
        return list(self._hits)


def _engine(channels, *, disabled=(), embedder=None):
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import Mode

    config = SLMConfig.for_mode(Mode.A)
    config.retrieval.use_cross_encoder = False
    # The engine reads this off the RETRIEVAL config, which is what it is
    # handed below — setting it on the parent silently does nothing.
    config.retrieval.disabled_channels = list(disabled)

    class _Embedder:
        def embed(self, text):
            return [0.1] * 8

    return RetrievalEngine(
        db=None,
        channels=channels,
        config=config.retrieval,
        embedder=embedder if embedder is not None else _Embedder(),
    )


@pytest.fixture
def statuses():
    """Collect the status dict from a real ``_run_channels`` call."""

    def _run(channels, *, disabled=(), embedder=None, q_emb=("default",)):
        eng = _engine(channels, disabled=disabled, embedder=embedder)
        collected: dict[str, str] = {}
        from superlocalmemory.retrieval.strategy import QueryStrategy

        strat = QueryStrategy(
            query_type="factual",
            weights={k: 1.0 for k in channels},
        )
        eng._run_channels(
            "any query", "default", strat,
            channel_status=collected,
        )
        return collected

    return _run


# ---------------------------------------------------------------------------

def test_a_channel_that_raises_is_reported_as_an_error(statuses):
    out = statuses({"bm25": _Channel("raise")})
    assert out["bm25"] == chstat.ERROR


def test_a_channel_that_finds_nothing_is_reported_as_empty(statuses):
    out = statuses({"bm25": _Channel("ok", hits=[])})
    assert out["bm25"] == chstat.EMPTY


def test_a_channel_that_finds_something_is_reported_as_ok(statuses):
    out = statuses({"bm25": _Channel("ok", hits=[("f1", 0.9)])})
    assert out["bm25"] == chstat.OK


def test_error_and_empty_are_not_the_same_value(statuses):
    """The whole point, stated as one assertion."""
    broken = statuses({"bm25": _Channel("raise")})["bm25"]
    quiet = statuses({"bm25": _Channel("ok", hits=[])})["bm25"]
    assert broken != quiet


def test_a_channel_that_is_not_built_says_so(statuses):
    out = statuses({"bm25": _Channel("ok", hits=[("f1", 0.5)])})
    assert out["semantic"] == chstat.NOT_CONFIGURED
    assert out["hopfield"] == chstat.NOT_CONFIGURED


def test_an_ablated_channel_reads_as_a_choice_not_a_fault(statuses):
    out = statuses(
        {"bm25": _Channel("ok", hits=[("f1", 0.5)])}, disabled=("bm25",),
    )
    assert out["bm25"] == chstat.DISABLED
    assert not chstat.is_fault(out["bm25"])


def test_channels_that_never_ran_because_embedding_failed_say_so(statuses):
    """The failure this field exists for.

    A dead embedder takes three of the five channels down at once, and the
    answer used to look exactly like a store with nothing relevant in it.
    """

    class _DeadEmbedder:
        def embed(self, text):
            raise RuntimeError("embedding provider is down")

    out = statuses(
        {
            "semantic": _Channel("ok", hits=[("f1", 0.9)]),
            "hopfield": _Channel("ok", hits=[("f2", 0.8)]),
            "spreading_activation": _Channel("ok", hits=[("f3", 0.7)]),
            "bm25": _Channel("ok", hits=[("f4", 0.6)]),
        },
        embedder=_DeadEmbedder(),
    )
    assert out["semantic"] == chstat.NO_EMBEDDING
    assert out["hopfield"] == chstat.NO_EMBEDDING
    assert out["spreading_activation"] == chstat.NO_EMBEDDING
    # The one channel that needs no embedding is unaffected, which is the
    # distinction a single "unavailable" status would have thrown away.
    assert out["bm25"] == chstat.OK
    assert chstat.is_fault(out["semantic"])


def test_every_dispatched_channel_gets_exactly_one_status(statuses):
    """A missing key is a reporting gap and must never happen.

    ``empty`` is an answer. No key at all is the old behaviour wearing a new
    field, and it is what a partial implementation looks like.
    """
    out = statuses({
        "semantic": _Channel("ok", hits=[("a", 0.9)]),
        "bm25": _Channel("raise"),
        "temporal": _Channel("ok", hits=[]),
        "hopfield": _Channel("ok", hits=[("b", 0.4)]),
        "spreading_activation": _Channel("raise"),
    })
    for name in (
        "semantic", "bm25", "temporal", "hopfield", "spreading_activation",
    ):
        assert name in out, f"{name} reported nothing at all"
        assert out[name] in chstat.ALL_STATUSES, f"{name}={out[name]!r}"


def test_a_hung_channel_is_a_timeout_not_an_error(statuses, monkeypatch):
    """Timeout and error are different problems with different remedies.

    Drives the real hang guard by shortening it, rather than mocking the wait.
    """
    import superlocalmemory.retrieval.engine as engine_mod

    monkeypatch.setattr(engine_mod, "CHANNEL_HANG_GUARD_SECONDS", 0.05)
    out = statuses({
        "bm25": _Channel("ok", hits=[("slow", 0.5)], delay=1.5),
        "temporal": _Channel("ok", hits=[("fast", 0.9)]),
    })
    assert out["bm25"] == chstat.TIMEOUT
    assert out["temporal"] == chstat.OK
    assert out["bm25"] != chstat.ERROR


def test_the_timeout_report_and_the_incomplete_list_cannot_disagree(monkeypatch):
    """Both are written in one branch; this proves they stayed that way."""
    import superlocalmemory.retrieval.engine as engine_mod
    from superlocalmemory.retrieval.strategy import QueryStrategy

    monkeypatch.setattr(engine_mod, "CHANNEL_HANG_GUARD_SECONDS", 0.05)
    eng = _engine({
        "bm25": _Channel("ok", hits=[("slow", 0.5)], delay=1.5),
        "temporal": _Channel("ok", hits=[("fast", 0.9)]),
    })
    dropped: set[str] = set()
    status: dict[str, str] = {}
    eng._run_channels(
        "any query", "default",
        QueryStrategy(query_type="factual", weights={"bm25": 1.0}),
        dropped_channels=dropped,
        channel_status=status,
    )
    timed_out = {n for n, s in status.items() if s == chstat.TIMEOUT}
    assert timed_out == dropped, f"{timed_out} vs {dropped}"


# ---------------------------------------------------------------------------
# The other two layers. The engine reporting a status nobody can see would be
# the same defect wearing a new field, and this trio — engine, response,
# serialised payload — is exactly where a dropped marker hid once before.
# ---------------------------------------------------------------------------

def test_the_status_reaches_the_response(engine_with_mock_deps):
    from tests.conftest import force_sync_enrichment

    engine = force_sync_enrichment(engine_with_mock_deps)
    engine.store("The build cache lives on the Frankfurt runner.")
    response = engine.recall("build cache")

    assert response.channel_status, "the response carries no channel report"
    for name, status in response.channel_status.items():
        assert status in chstat.ALL_STATUSES, f"{name}={status!r}"
    # Every channel that contributed candidates must be accounted for; a
    # channel present in the answer but absent from the report is a gap.
    for name in response.channel_weights:
        if name in chstat.CHANNEL_NAMES:
            assert name in response.channel_status, f"{name} unreported"


def test_a_broken_channel_is_visible_in_the_response(engine_with_mock_deps):
    """End to end: break one channel, read the answer, see which one broke."""
    from tests.conftest import force_sync_enrichment

    engine = force_sync_enrichment(engine_with_mock_deps)
    engine.store("The build cache lives on the Frankfurt runner.")

    bm25 = engine._retrieval_engine._bm25
    assert bm25 is not None, "fixture has no lexical channel to break"

    def _explode(*args, **kwargs):
        raise RuntimeError("lexical index is corrupt")

    original = bm25.search
    bm25.search = _explode
    try:
        response = engine.recall("build cache")
    finally:
        bm25.search = original

    assert response.channel_status.get("bm25") == chstat.ERROR
    assert chstat.is_fault(response.channel_status["bm25"])


def test_the_serialised_answer_carries_the_status():
    """Because a caller on the wire is the one who cannot read a server log."""
    from superlocalmemory.server.recall_serializer import recall_response_metadata
    from superlocalmemory.storage.models import RecallResponse

    response = RecallResponse(
        query="anything",
        channel_status={"bm25": chstat.ERROR, "semantic": chstat.OK},
    )
    meta = recall_response_metadata(response)
    assert meta["channel_status"] == {"bm25": "error", "semantic": "ok"}


def test_a_response_without_the_field_still_serialises():
    """Older callers and older stored responses must not break the surface."""
    from superlocalmemory.server.recall_serializer import recall_response_metadata

    class _Legacy:
        query = "anything"

    meta = recall_response_metadata(_Legacy())
    assert meta["channel_status"] == {}


# ---------------------------------------------------------------------------
# Statuses that must not be confused with each other
# ---------------------------------------------------------------------------

def test_an_ablated_profile_channel_is_reported_as_ablated(engine_with_mock_deps):
    """Switching a channel off must not read as the channel finding nothing.

    The profile channel ignored the ablation flag entirely: it still searched,
    still doubled its own weight on a hit, and reported `ok` or `empty`. An
    operator comparing that against their own configuration would be told their
    setting had no effect — and could not tell it from a live channel that had
    nothing to say.
    """
    from tests.conftest import force_sync_enrichment

    engine = force_sync_enrichment(engine_with_mock_deps)
    engine.store("The build cache lives on the Frankfurt runner.")

    retrieval = engine._retrieval_engine
    retrieval._config.disabled_channels = list(
        retrieval._config.disabled_channels) + ["profile"]
    try:
        response = engine.recall("build cache")
    finally:
        retrieval._config.disabled_channels = [
            c for c in retrieval._config.disabled_channels if c != "profile"
        ]

    assert response.channel_status.get("profile") == chstat.DISABLED
    assert not chstat.is_fault(response.channel_status["profile"])
    assert "profile" not in (response.channel_weights or {}) or \
        response.channel_weights.get("profile") != 2.0, (
            "an ablated channel still had its weight doubled"
        )


def test_a_channel_with_nothing_to_score_did_not_find_nothing():
    """"empty" claims a search happened. Some channels never search.

    The graph channel re-scores what the other channels found. With nothing
    fused it never ran — and if the reason nothing fused is that the others
    failed, reporting this one as having searched and come back empty hides the
    actual fault behind a status that `is_fault` calls fine.
    """
    assert chstat.NO_CANDIDATES in chstat.ALL_STATUSES
    assert chstat.NO_CANDIDATES != chstat.EMPTY
    assert not chstat.is_fault(chstat.NO_CANDIDATES)


def test_every_reportable_channel_has_a_status_not_just_the_producers(
    engine_with_mock_deps,
):
    """The earlier version checked only the five dispatched producers.

    `profile` and `entity_graph` are recorded elsewhere in `recall`, so a
    missing key for either slipped through a check that iterated the producers,
    and through one that intersected with `channel_weights`.
    """
    from tests.conftest import force_sync_enrichment

    engine = force_sync_enrichment(engine_with_mock_deps)
    engine.store("The build cache lives on the Frankfurt runner.")
    response = engine.recall("build cache")

    for name in ("profile", "entity_graph"):
        assert name in response.channel_status, (
            f"{name} reported no status at all; it is in CHANNEL_NAMES, so a "
            f"consumer reading the report sees a gap rather than an answer"
        )
        assert response.channel_status[name] in chstat.ALL_STATUSES
