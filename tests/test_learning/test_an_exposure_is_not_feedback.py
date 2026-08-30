# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""Two counters that were measuring the wrong thing, and one scale that was wrong.

SHOWING A MEMORY IS NOT EVIDENCE THAT IT HELPED. ``count_signals`` counted
every ``learning_signals`` row, and almost every row is an exposure written once
per fact displayed at recall. Measured on a live production store::

    candidate         5,340      exposure
    legacy_feedback       2      the only real feedback
                      ─────
                      5,342      what count_signals() returned

A 2,670x inflation. Every surface that resolves a ranking phase from that number
believed it had Phase 3 data — LightGBM active — on two feedback events, and the
model it activated had been trained on 972 rows whose labels were all 0.0. It
reordered results at random and displaced the heuristic that would otherwise
have been used. Correcting the count drops the system to Phase 1, which is the
honest state and the better ranker.

AND A CHANNEL THAT COULD OUTVOTE THE OTHERS BY UNITS. ``apply_channel_weights``
sums ``channel_scores[ch] * weights[ch]``. Semantic cosine, Fisher-Rao and the
temporal proximity score are all bounded [0, 1]; BM25 was not. Measured maxima
on a live store, per query: 2.845, 3.865, 10.150. So the sum was decided by
a scale, and any weight the bandit learned was a correction for that scale —
wrong as soon as the query length changed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.learning.signal_kinds import (
    EXPOSURE_SIGNAL_TYPES,
    FEEDBACK_ONLY_SQL,
    is_feedback,
)

_PROFILE = "default"


@pytest.fixture()
def learning_db(tmp_path: Path) -> Path:
    """100 exposures, 3 legacy feedback, 2 of a kind that does not exist yet."""
    db = tmp_path / "learning.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE learning_signals ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL,"
        " query TEXT NOT NULL, fact_id TEXT NOT NULL,"
        " signal_type TEXT NOT NULL, value REAL DEFAULT 1.0,"
        " created_at TEXT NOT NULL, query_id TEXT DEFAULT '',"
        " position INTEGER DEFAULT 0)"
    )
    for kind, count in (("candidate", 100), ("shown", 7),
                        ("legacy_feedback", 3), ("dwell", 2)):
        for _ in range(count):
            conn.execute(
                "INSERT INTO learning_signals (profile_id, query, fact_id,"
                " signal_type, created_at) VALUES (?,'q','f',?, '2026-01-01')",
                (_PROFILE, kind),
            )
    conn.commit()
    conn.close()
    return db


class TestTheCounterThatGatesARankingPhase:
    def test_exposures_are_excluded(self, learning_db: Path) -> None:
        from superlocalmemory.core.recall_pipeline import _ReadOnlyLearningView

        assert _ReadOnlyLearningView(learning_db).count_signals(_PROFILE) == 5, (
            "112 rows were seeded and only 5 are feedback; anything else means "
            "exposures are still being counted as evidence"
        )

    def test_both_implementations_agree(self, learning_db: Path) -> None:
        """A phase resolved from one is compared to a threshold from the other.

        Two copies of this predicate is how they drift, and they had already
        drifted into two separate literals before this was shared.
        """
        from superlocalmemory.core.recall_pipeline import _ReadOnlyLearningView
        from superlocalmemory.learning.database import LearningDatabase

        view = _ReadOnlyLearningView(learning_db).count_signals(_PROFILE)
        direct = LearningDatabase(learning_db).count_signals(_PROFILE)
        assert view == direct == 5

    def test_a_new_feedback_kind_counts_without_being_registered(
        self, learning_db: Path,
    ) -> None:
        """``dwell`` appears nowhere in the codebase and still counts.

        The predicate excludes exposures rather than naming feedback, so the
        failure mode of forgetting to update it is under-counting a new kind —
        not silently re-admitting 5,340 exposures.
        """
        assert is_feedback("dwell") is True
        assert is_feedback("explicit") is True
        for kind in EXPOSURE_SIGNAL_TYPES:
            assert is_feedback(kind) is False

    def test_shown_is_treated_as_an_exposure(self) -> None:
        """Excluding only 'candidate' would leave this hole.

        ``learning/database.py`` filters training rows on
        ``signal_type IN ('candidate', 'shown', 'legacy_feedback')``, so
        ``shown`` is a real kind the system writes alongside ``candidate``.
        ``!= 'candidate'`` would have counted it as feedback.
        """
        assert "shown" in EXPOSURE_SIGNAL_TYPES
        assert "shown" in FEEDBACK_ONLY_SQL

    def test_neither_counter_carries_its_own_copy_of_the_predicate(self) -> None:
        import inspect

        from superlocalmemory.core import recall_pipeline
        from superlocalmemory.learning import database

        for mod in (recall_pipeline, database):
            src = inspect.getsource(mod)
            assert "signal_type NOT IN" not in src, (
                f"{mod.__name__} inlines the predicate instead of importing "
                "FEEDBACK_ONLY_SQL"
            )

    def test_the_training_row_filter_is_untouched(self) -> None:
        """It is a different question with a different answer.

        ``_fetch_training_rows`` deliberately includes exposures — a ranker
        learns from what was shown as well as what was chosen. Only the PHASE
        COUNTER was wrong. Narrowing that query too would have starved training.
        """
        import inspect

        from superlocalmemory.learning import database

        src = inspect.getsource(database)
        assert src.count("signal_type IN ('candidate', 'shown', 'legacy_feedback')") >= 1


class TestTheLexicalChannelSharesAScaleWithTheOthers:
    def test_scores_are_bounded_below_one(self) -> None:
        from superlocalmemory.retrieval.bm25_channel import _to_unit_scale

        for raw in (0.1, 2.676, 2.845, 3.865, 10.150, 23.6, 1_000.0):
            got = _to_unit_scale([("f", raw)])[0][1]
            assert 0.0 < got < 1.0, (raw, got)

    def test_order_is_never_disturbed(self) -> None:
        from superlocalmemory.retrieval.bm25_channel import _to_unit_scale

        raw = [("a", 23.6), ("b", 16.8), ("c", 5.1), ("d", 2.676), ("e", 0.4)]
        out = _to_unit_scale(raw)
        assert [f for f, _ in out] == [f for f, _ in raw]
        vals = [v for _, v in out]
        assert all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))

    def test_a_lone_weak_hit_does_not_become_certain(self) -> None:
        """The regression an existing test caught before this shipped.

        The first implementation divided by the batch maximum, which makes the
        best result exactly 1.0 whatever it scored. A query with one weak
        lexical match then reported full confidence and outranked the semantic
        channel — breaking the scenario
        ``test_real_fts5_exact_hit_keeps_bounded_slot`` was written to protect.
        """
        from superlocalmemory.retrieval.bm25_channel import _to_unit_scale

        weak = _to_unit_scale([("only", 0.8)])[0][1]
        strong = _to_unit_scale([("only", 20.0)])[0][1]
        assert weak < 0.5 < strong, (weak, strong)

    def test_the_same_score_maps_the_same_way_every_time(self) -> None:
        """Batch-relative scaling made a fact's score depend on its neighbours.

        The same query returning a different number because a different set of
        facts came back is a repeatability defect, which HARD-RULES RULE 6 ranks
        above any latency concern.
        """
        from superlocalmemory.retrieval.bm25_channel import _to_unit_scale

        alone = _to_unit_scale([("f", 5.1)])[0][1]
        crowded = dict(_to_unit_scale(
            [("x", 40.0), ("f", 5.1), ("y", 0.2)],
        ))["f"]
        assert alone == pytest.approx(crowded)

    def test_no_lexical_evidence_contributes_nothing_to_the_sum(self) -> None:
        from superlocalmemory.retrieval.bm25_channel import _to_unit_scale

        assert _to_unit_scale([("f", 0.0)])[0][1] == 0.0
        assert _to_unit_scale([("f", -3.0)])[0][1] == 0.0

    def test_both_search_paths_are_rescaled(self) -> None:
        """FTS5 is the fast path; rank_bm25 is the legacy fallback.

        The fallback applies a 1.5x exact-phrase bonus, so its raw ceiling is
        higher still. Rescaling one and not the other would mean the scale
        depended on which path a store happened to take.
        """
        import inspect

        from superlocalmemory.retrieval import bm25_channel

        src = inspect.getsource(bm25_channel.BM25Channel.search)
        assert src.count("_to_unit_scale(") == 2, (
            "one of the two return paths is unscaled"
        )


class TestRetrainRefusesAZeroSignalCorpus:
    """The refusal was implemented and never tested. That was found later.

    The abort reason ``insufficient_label_signal`` appeared in zero tests. Two
    existing retrain tests were merely padded from 30 and 40 rows to 60 and 120
    so they cleared the new gate — which exercises the gate being *passed*, not
    the gate *refusing*. Those are different code paths and only one of them was
    the point.
    """

    @staticmethod
    def _rows(n: int, reward: float | None) -> list[dict]:
        from superlocalmemory.learning import consolidation_worker as cw

        return [
            {"query_id": "q1", "fact_id": f"f{i}", "position": i,
             "features": {name: 0.0 for name in cw._feature_names()},
             "outcome_reward": reward}
            for i in range(n)
        ]

    def _cycle(self, monkeypatch, tmp_path, rows: list[dict]) -> dict:
        from superlocalmemory.learning import consolidation_worker as cw

        monkeypatch.setattr(cw, "_fetch_training_rows", lambda db, pid: (rows, []))
        monkeypatch.setattr(
            cw, "_train_booster",
            lambda *a, **k: pytest.fail(
                "training started on a corpus the gate should have refused"
            ),
        )
        return cw._run_shadow_cycle(
            memory_db_path=str(tmp_path / "memory.db"),
            learning_db_path=str(tmp_path / "learning.db"),
            profile_id="p1",
        )

    def test_the_live_shape_is_refused(self, monkeypatch, tmp_path) -> None:
        """Every training row carrying no outcome at all — the measured shape.

        5,000 rows fetched from the real store, ``outcome_reward`` None on all
        5,000: nothing has ever been reported, so there is nothing to learn
        from. The predicate first proposed for this gate,
        ``len(set(labels)) <= 1``, inspected a different column entirely and
        would have let training proceed.

        Writing this test is what corrected the number in the release record. It
        had said "2 informative labels", read off ``learning_features.label``;
        the gate reads ``outcome_reward``, and the honest figure is 0.
        """
        out = self._cycle(monkeypatch, tmp_path, self._rows(5000, None))
        assert out["aborted"] == "insufficient_label_signal", out
        assert out["candidate_persisted"] is False
        assert out["metrics"]["informative_labels"] == 0
        assert out["metrics"]["required"] == 50
        assert out["metrics"]["rows"] == 5000

    def test_a_failure_is_informative_even_though_it_is_zero(
        self, monkeypatch, tmp_path,
    ) -> None:
        """0.0 means "this did not help" — that is evidence, not absence.

        Worth pinning because the obvious implementation of "informative" is
        "truthy", which would silently discard every negative outcome and leave
        the ranker able to learn only what works, never what does not.
        """
        from superlocalmemory.learning import consolidation_worker as cw

        rows = self._rows(60, 0.0)
        monkeypatch.setattr(cw, "_fetch_training_rows", lambda db, pid: (rows, []))
        assert cw._count_informative_labels(rows) == 60

    def test_the_neutral_default_is_not_a_label(self, monkeypatch, tmp_path) -> None:
        """0.5 is what settlement writes when NO outcome was reported.

        A corpus of those says nothing happened, however many rows it has.
        """
        out = self._cycle(monkeypatch, tmp_path, self._rows(500, 0.5))
        assert out["aborted"] == "insufficient_label_signal", out
        assert out["metrics"]["informative_labels"] == 0

    def test_a_missing_reward_column_is_not_a_label(
        self, monkeypatch, tmp_path,
    ) -> None:
        """``outcome_reward`` is None before M006 runs."""
        out = self._cycle(monkeypatch, tmp_path, self._rows(500, None))
        assert out["aborted"] == "insufficient_label_signal", out

    def test_it_stops_refusing_once_the_signal_arrives(
        self, monkeypatch, tmp_path,
    ) -> None:
        """The other half of the gate. A refusal that never lifts is a wall.

        Note the trained path is NOT stubbed to fail here — reaching it is the
        expected outcome, so this test would catch a gate that refuses forever.
        """
        from superlocalmemory.learning import consolidation_worker as cw

        rows = self._rows(60, 1.0) + self._rows(60, 0.0)
        monkeypatch.setattr(cw, "_fetch_training_rows", lambda db, pid: (rows, []))
        reached = {"train": False}

        def _train(*_a, **_k):
            reached["train"] = True
            raise RuntimeError("stop here — reaching training is the assertion")

        monkeypatch.setattr(cw, "_train_booster", _train)
        out = cw._run_shadow_cycle(
            memory_db_path=str(tmp_path / "memory.db"),
            learning_db_path=str(tmp_path / "learning.db"),
            profile_id="p1",
        )
        assert reached["train"], (
            f"120 informative labels still did not clear the gate: {out}"
        )
        assert out["aborted"] != "insufficient_label_signal"
