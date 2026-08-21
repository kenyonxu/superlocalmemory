# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""No stage that shapes the answer may be chosen by a stopwatch.

Two stages in recall used to be skipped when the elapsed time had passed a
threshold: scene expansion at 0.8 s, which APPENDS candidates, and the
entity-graph boost at 0.9 s, which RE-SCORES every candidate and re-sorts them.
Neither is decoration -- both can change the top answer.

Recall's median on the 0.95 GB archive is ~1,044 ms, so those thresholds sat on
top of the median rather than out in the tail. Measured over two runs of 60
queries: the gates flipped their decision on 22 of 60, and of the 19 queries
whose answer changed, every single one had a flipped gate. Removing both moved
rank-1 disagreement from 20.0% to 3.3% and top-10 from 31.7% to 10.0%, costing
about 100-180 ms of p95 against a 2,000 ms ceiling.

These tests are deliberately about the defect CLASS, not those two lines. A
future latency push will be tempted to reintroduce exactly this pattern,
because it always looks like a free win: the percentile improves and no test
fails. What breaks is that the same question stops having the same answer.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from superlocalmemory.retrieval import engine as engine_mod

# Stages that shape the answer. A clock condition anywhere in recall is
# suspicious, but in these it is disqualifying, so they are named to keep the
# failure message specific about what breaks.
ANSWER_SHAPING = ("scene", "entity", "fused", "boost", "expansion")


def _recall_tree() -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(engine_mod.RetrievalEngine.recall)))


def _is_monotonic_call(node: ast.AST) -> bool:
    """time.monotonic() / _time_e.monotonic() / perf_counter(), however aliased."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    return name in {"monotonic", "perf_counter", "time"}


def _clock_comparisons(tree: ast.AST) -> list[ast.Compare]:
    """Comparisons with a clock reading on either side.

    Matches the real shape -- `(monotonic() - _e0) < 0.8` -- where the clock
    call is nested inside a BinOp, not a direct operand.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if any(_is_monotonic_call(sub) for sub in ast.walk(operand)):
                found.append(node)
                break
    return found


def _guarded_bodies(tree: ast.AST) -> list[tuple[ast.If, str]]:
    """Every `if` whose test reads a clock, with its body rendered as text."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _clock_comparisons(node.test):
            body = " ".join(ast.dump(s) for s in node.body).lower()
            out.append((node, body))
    return out


class TestNoStageIsGatedOnElapsedTime:
    def test_recall_has_no_clock_gated_branch(self) -> None:
        """The regression itself.

        An `if` in recall that tests elapsed time and then does work is the
        pattern that made the same query return different answers depending on
        machine load.
        """
        guarded = _guarded_bodies(_recall_tree())
        assert guarded == [], (
            "recall() branches on elapsed time: "
            + "; ".join(
                f"line {node.lineno} guards {sorted({w for w in ANSWER_SHAPING if w in body}) or 'unknown work'}"
                for node, body in guarded
            )
            + " — the same question will return different answers under load"
        )

    def test_no_answer_shaping_stage_is_behind_a_clock(self) -> None:
        """Narrower and blunter, so the failure names the harm.

        Kept alongside the test above because a clock gate around, say, a debug
        log is untidy rather than incorrect; one around scene expansion or the
        entity boost changes which memory the user is handed.
        """
        offenders = [
            (node.lineno, sorted({w for w in ANSWER_SHAPING if w in body}))
            for node, body in _guarded_bodies(_recall_tree())
            if any(w in body for w in ANSWER_SHAPING)
        ]
        assert offenders == [], (
            f"answer-shaping work is gated on the clock at {offenders}; bound it "
            "by data (a candidate count, a cap) so the same input takes the "
            "same path"
        )

    def test_the_detector_can_actually_fail(self) -> None:
        """Without this, the two tests above could be passing vacuously.

        An AST matcher that silently matches nothing certifies whatever it is
        pointed at. This feeds it the exact shape that was removed -- the clock
        call nested inside a BinOp inside the comparison -- and requires a hit.
        """
        sample = textwrap.dedent(
            """
            def recall(self):
                if fused and (_time_e.monotonic() - _e0) < 0.8:
                    scenes_map = self._db.get_scenes_for_facts_batch(ids, pid)
            """
        )
        tree = ast.parse(sample)
        guarded = _guarded_bodies(tree)
        assert len(guarded) == 1, (
            "the detector cannot see the very pattern it was written for, so "
            "its passing verdict on the real source means nothing"
        )
        assert any(w in guarded[0][1] for w in ANSWER_SHAPING)

    def test_the_detector_does_not_fire_on_a_plain_timing_measurement(self) -> None:
        """Recording a duration is fine; branching the answer on it is not.

        recall() legitimately measures its own elapsed time for the response's
        `retrieval_time_ms` and for the optional timing log. A detector that
        flagged those would be turned off by the next person to see it fail.
        """
        sample = textwrap.dedent(
            """
            def recall(self):
                ms = (time.monotonic() - t0) * 1000.0
                logger.warning("took %.0f ms", ms)
                return ms
            """
        )
        assert _guarded_bodies(ast.parse(sample)) == []


class TestTheStagesStillRun:
    """Removing a gate must not become removing the stage.

    Deleting the scene expansion and entity boost outright would satisfy every
    test above -- no clock gate can remain in code that no longer exists -- while
    quietly dropping two sources of answer quality.
    """

    @pytest.mark.parametrize(
        "marker",
        [
            "get_scenes_for_facts_batch",   # scene expansion
            "score_candidates",             # entity-graph boost
        ],
    )
    def test_the_stage_is_still_reached(self, marker: str) -> None:
        src = inspect.getsource(engine_mod.RetrievalEngine.recall)
        assert marker in src, (
            f"{marker} is gone from recall(); the clock gate was removed by "
            "deleting the stage it guarded"
        )
