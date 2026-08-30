# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Trust earned by an agent must still be there after a restart.

WHAT WAS MEASURED

On a real store, 58 agent trust records. Fifty-one of them were keyed
``daemon-capability:<fingerprint>`` — one per daemon start — holding 594
evidence events between them, none accumulating, each beginning again at the
neutral prior. One further record, the catch-all ``unknown`` bucket, held 1,708
events at a trust of 1.000: every unidentified caller pooled into one identity
that then scored higher than any real one.

WHAT THAT COST, EXACTLY

Nothing that gates. The write threshold is 0.3, delete is 0.5, and an unseen
identity reads the 0.5 prior — so a fresh fingerprint after a restart could
always write. Every one of the 51 stranded records scored between 0.947 and
0.990, above the prior, so their history could only ever have raised trust. The
loss is reputation, not access, and that is why the record here is a measurement
rather than a migration: rewriting what identity a write is attributed to also
rewrites audit attribution and provenance, which is not a change to make for a
cost of zero gated operations.

The catch-all was the half that mattered, and it is closed: an identifier that
names nobody reads as the prior no matter what has been written against it.

These tests pin both properties.
"""

from __future__ import annotations

import pytest

from superlocalmemory.trust.scorer import ANONYMOUS_IDENTITIES, TrustScorer


@pytest.fixture()
def scorer(engine_with_mock_deps):
    return TrustScorer(engine_with_mock_deps._db)


class TestACatchAllIsNotAnIdentity:
    """1,708 events pooled under one name, scoring above every real agent."""

    def test_the_pooled_bucket_reads_as_the_prior_however_high_it_is_written(
        self, scorer,
    ) -> None:
        for _ in range(40):
            scorer.record_signal("unknown", "default", "store_success")

        assert scorer.get_agent_trust("unknown", "default") == pytest.approx(0.5)

    def test_every_anonymous_spelling_is_treated_the_same(self, scorer) -> None:
        """One of these being missed puts the whole bucket back in play."""
        for name in ANONYMOUS_IDENTITIES:
            for _ in range(10):
                scorer.record_signal(name, "default", "store_success")
            assert scorer.get_agent_trust(name, "default") == pytest.approx(0.5), (
                f"{name!r} still accumulates trust as though it were somebody"
            )

    def test_a_named_agent_is_unaffected_by_the_rule(self, scorer) -> None:
        for _ in range(20):
            scorer.record_signal("claude", "default", "store_success")

        assert scorer.get_agent_trust("claude", "default") > 0.5


class TestTrustSurvivesARestart:
    """A restart is not a new agent."""

    def test_a_stable_identity_keeps_what_it_earned(
        self, engine_with_mock_deps,
    ) -> None:
        """Two scorers over one store stand in for two runs of the process.

        The scorer holds no state of its own, so a second instance reading the
        same store is exactly what the next daemon start does.
        """
        db = engine_with_mock_deps._db
        first = TrustScorer(db)
        for _ in range(15):
            first.record_signal("slm-daemon", "default", "store_success")
        earned = first.get_agent_trust("slm-daemon", "default")
        assert earned > 0.5

        after_restart = TrustScorer(db)

        assert after_restart.get_agent_trust("slm-daemon", "default") == pytest.approx(
            earned
        )

    def test_a_per_restart_identity_starts_over_and_that_is_the_defect(
        self, engine_with_mock_deps,
    ) -> None:
        """Naming the behaviour so a change to it is visible, not silent.

        This is what the 51 records on the real store were doing. It is recorded
        rather than fixed because it gates nothing: the fresh identity reads the
        0.5 prior, which clears the 0.3 write threshold, so the only loss is the
        reputation the previous fingerprint had earned.
        """
        db = engine_with_mock_deps._db
        scorer = TrustScorer(db)
        for _ in range(15):
            scorer.record_signal(
                "daemon-capability:aaaaaaaaaaaa", "default", "store_success",
            )
        earned = scorer.get_agent_trust("daemon-capability:aaaaaaaaaaaa", "default")

        fresh = scorer.get_agent_trust("daemon-capability:bbbbbbbbbbbb", "default")

        assert earned > 0.5
        assert fresh == pytest.approx(0.5), "a new fingerprint reads the prior"
        assert fresh < earned

    def test_the_prior_still_clears_the_write_threshold(self) -> None:
        """Which is why the fragmentation costs reputation and not availability.

        If this ever stops holding, a restart would start refusing writes, and
        the fragmentation stops being cosmetic.
        """
        from superlocalmemory.trust.gate import (
            _DEFAULT_DELETE_THRESHOLD,
            _DEFAULT_WRITE_THRESHOLD,
        )
        from superlocalmemory.trust.scorer import _DEFAULT_TRUST

        assert _DEFAULT_TRUST >= _DEFAULT_WRITE_THRESHOLD, (
            "a fresh identity can no longer write — restart fragmentation has "
            "become an availability defect rather than a reputation one, and the "
            "decision not to migrate the history no longer holds"
        )
        assert _DEFAULT_TRUST >= _DEFAULT_DELETE_THRESHOLD, (
            "a fresh identity can no longer delete; same conclusion as above"
        )
