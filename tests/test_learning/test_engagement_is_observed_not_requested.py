"""Reward comes from what an agent did, and abstains when it did nothing visible.

The ladder this replaces asked whether a recalled ``fact_id`` appeared verbatim
in a later tool event, and answered ``0.5`` whenever it could not tell. Nothing
makes a caller echo that marker, so in practice no signal was ever
registered.

The default was the worse half. ``alpha += 0.5`` with ``beta += 0.5`` keeps a
Beta mean at 0.5 while shrinking its variance, so repeated empty settlements
make an arm *more* certain it is average and harder to move once evidence
arrives, leaving every arm sitting on its prior.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from superlocalmemory.learning.engagement_features import (
    EngagementFeatures,
    extract_features,
    tokenize,
)
from superlocalmemory.learning.propensity import MAX_WEIGHT, estimate_propensity, ips_weight
from superlocalmemory.learning.reward_model import score

_RECALLED = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
_SESSION = "01a01bba-5f09-7a61-86f5-d67511c89283"


@pytest.fixture
def store(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.execute(
        "CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, content TEXT, "
        "entities_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE tool_events (id INTEGER PRIMARY KEY, session_id TEXT, "
        "profile_id TEXT, tool_name TEXT, input_summary TEXT, "
        "output_summary TEXT, created_at TEXT)"
    )
    conn.commit()
    yield conn
    conn.close()


def _fact(conn, fact_id, content, entities="[]"):
    conn.execute("INSERT INTO atomic_facts VALUES (?,?,?)", (fact_id, content, entities))
    conn.commit()


def _event(conn, tool, payload, offset_sec, session=_SESSION):
    conn.execute(
        "INSERT INTO tool_events (session_id, profile_id, tool_name, "
        "input_summary, output_summary, created_at) VALUES (?,?,?,?,?,?)",
        (session, "default", tool, payload, "",
         (_RECALLED + timedelta(seconds=offset_sec)).isoformat()),
    )
    conn.commit()


def _extract(conn, **kw):
    return extract_features(
        conn, session_id=kw.pop("session", _SESSION), profile_id="default",
        fact_ids=["f1"], recalled_at=_RECALLED, **kw,
    )


class TestItObservesWithoutBeingAsked:
    def test_a_later_action_naming_the_memory_counts_as_engagement(self, store):
        """No marker, no cooperation — the agent simply worked on the subject."""
        _fact(store, "f1", "The projection drain reads projection_outbox in memory.db")
        _event(store, "Bash", "grep projection_outbox memory.db drain", 30)

        features = _extract(store)

        assert features.peak_overlap > 0.0
        assert features.matched_fact_ids == ["f1"]
        assert features.observed is True

    def test_reaching_a_written_artifact_scores_above_merely_appearing(self, store):
        _fact(store, "f1", "The projection drain reads projection_outbox in memory.db")
        _event(store, "Write", "projection_outbox drain memory.db projection", 20)

        written = score(_extract(store))

        store.execute("DELETE FROM tool_events")
        store.commit()
        _event(store, "Bash", "projection_outbox drain memory.db projection", 20)
        mentioned = score(_extract(store))

        assert written.reward > mentioned.reward
        assert written.kind == "artifact_overlap"

    def test_another_conversation_is_not_this_ones_engagement(self, store):
        """Without the conversation predicate a busy machine reads as success."""
        _fact(store, "f1", "The projection drain reads projection_outbox in memory.db")
        _event(store, "Write", "projection_outbox drain memory.db", 20,
               session="a-different-conversation")

        assert _extract(store).observed is False

    def test_action_outside_the_window_is_not_engagement(self, store):
        _fact(store, "f1", "The projection drain reads projection_outbox in memory.db")
        _event(store, "Write", "projection_outbox drain memory.db", 10_000)

        assert _extract(store).observed is False

    def test_common_words_alone_are_not_overlap(self, store):
        """Without stopword removal every payload overlaps every memory."""
        _fact(store, "f1", "This is the thing that it was for and about")
        _event(store, "Bash", "this is the thing that it was for and about", 10)

        assert _extract(store).peak_overlap == 0.0

    def test_tokenizer_keeps_identifiers_and_drops_filler(self):
        tokens = tokenize("The daemon writes pending_outcomes to memory.db and it is fine")
        assert {"pending_outcomes", "memory.db", "daemon", "writes"} <= tokens
        assert not ({"the", "to", "and", "it", "is"} & tokens)


class TestItRefusesToInventANumber:
    def test_nothing_observed_abstains(self):
        decision = score(EngagementFeatures(action_count=5))
        assert decision.reward is None
        assert decision.abstained is True

    def test_no_observation_ever_produces_the_neutral_value(self):
        """0.5 is reserved for "no information" and must never be emitted:
        it is the one value that moves alpha and beta together."""
        cases = [
            EngagementFeatures(action_count=3),
            EngagementFeatures(requeried=True),
            EngagementFeatures(action_count=1, peak_overlap=0.001,
                               matched_fact_ids=["f1"]),
            EngagementFeatures(action_count=1, peak_overlap=1.0,
                               artifact_overlap=1.0, matched_fact_ids=["f1"]),
            EngagementFeatures(marker_hit=True),
        ]
        for features in cases:
            assert score(features).reward != pytest.approx(0.5)

    def test_a_requery_is_the_one_hard_zero(self):
        assert score(EngagementFeatures(requeried=True)).reward == 0.0

    def test_the_weakest_real_evidence_still_moves_the_arm_up(self):
        decision = score(EngagementFeatures(
            action_count=1, peak_overlap=0.01, matched_fact_ids=["f1"],
        ))
        assert decision.reward is not None and decision.reward > 0.5


class TestItWillNotConfirmItself:
    def test_an_arm_the_policy_rarely_shows_carries_more_weight(self):
        """IPS: engagement on an unlikely arm is stronger evidence than
        engagement on the arm the policy shows anyway."""
        favoured = ips_weight((20.0, 1.0), [(1.0, 20.0), (1.0, 20.0)])
        longshot = ips_weight((1.0, 20.0), [(20.0, 1.0), (20.0, 1.0)])

        assert longshot.weight > favoured.weight
        assert favoured.weight == pytest.approx(1.0, abs=0.05)

    def test_identical_arms_split_the_propensity_evenly(self):
        """Three indistinguishable arms: each is shown about a third of the
        time, which is the estimator's correctness check."""
        p = estimate_propensity((5.0, 5.0), [(5.0, 5.0), (5.0, 5.0)])
        assert p == pytest.approx(1 / 3, abs=0.05)

    def test_weight_is_clipped(self):
        """Unclipped IPS has unbounded variance; one rare event must not move a
        posterior by a thousand plays' worth."""
        assert ips_weight((1.0, 1e6), [(1e6, 1.0)]).weight <= MAX_WEIGHT

    def test_no_competitors_means_no_correction_and_says_so(self):
        estimate = ips_weight((5.0, 5.0), [])
        assert estimate.weight == 1.0
        assert estimate.corrected is False

    def test_the_estimate_is_reproducible(self):
        """A retry of the same settlement must weight it identically."""
        args = ((3.0, 4.0), [(2.0, 5.0), (6.0, 2.0)])
        assert ips_weight(*args).weight == ips_weight(*args).weight
