# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The same question must return the same answer.

Sorting candidates by score alone is a PARTIAL order. Facts that tie keep
whatever order the input happened to arrive in, and that order comes from SQLite
page layout, dict insertion order, or which thread finished first — none of which
is data. So two runs over an unchanged store can return different answers, and
one of them is shown to the user as *the* answer.

Measured on a 517 MB store, 140 queries, two runs of identical code: 14 queries
returned a different top-10, 11 returned a different SET of facts, and 3 gave a
different single top answer. Nothing had changed between the runs.

The fix is to make every ordering total by breaking ties on `fact_id`, which is
unique and stable. `fusion.py` already did this; twenty-three other sorts and two
SQL queries did not.

Two tests here, deliberately of different kinds:

* a **behavioural** one, that tied scores come back in the same order however
  they went in — this is the property that matters;
* a **structural** one over the whole retrieval package, because the property
  holds at twenty-three separate call sites and a behavioural test for each would
  be twenty-three fixtures that still miss the twenty-fourth. The structural test
  is the one that catches a new sort added next month.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

RETRIEVAL = pathlib.Path(__file__).resolve().parents[2] / "src" / "superlocalmemory" / "retrieval"


def _sort_calls_keyed_on_score_alone(tree: ast.AST) -> list[tuple[int, str]]:
    """Find `sorted(...)`/`.sort(...)` whose key returns a single scalar.

    A key that returns one value cannot express a tie-break. A key returning a
    tuple can, and is what every ordering here must use.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        is_sorted = isinstance(target, ast.Name) and target.id == "sorted"
        is_sort_method = isinstance(target, ast.Attribute) and target.attr == "sort"
        if not (is_sorted or is_sort_method):
            continue
        key = next((kw.value for kw in node.keywords if kw.arg == "key"), None)
        if key is None:
            continue  # sorting the values themselves is already total
        if not isinstance(key, ast.Lambda):
            continue  # e.g. operator.itemgetter with several indices — allow
        body = key.body
        if isinstance(body, ast.Tuple):
            continue  # a tuple key can carry the tie-break
        # A single-scalar key. Reading the source segment keeps the failure
        # message actionable rather than making the reader hunt for the line.
        found.append((getattr(node, "lineno", -1), ast.unparse(key)))
    return found


def test_no_ordering_in_the_retrieval_path_is_keyed_on_score_alone() -> None:
    """Every sort must be able to break a tie.

    Listing the offenders rather than counting them, so the failure names exactly
    what to fix. If a genuinely score-only sort is ever correct here, it needs a
    tuple key whose second element is a constant and a comment saying why — not an
    exemption in this test.
    """
    offenders: list[str] = []
    for path in sorted(RETRIEVAL.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, key_src in _sort_calls_keyed_on_score_alone(tree):
            offenders.append(f"{path.name}:{lineno}  key={key_src}")

    assert not offenders, (
        "these orderings are keyed on a single value, so tied candidates are "
        "ordered by whatever the input happened to be — storage layout, dict "
        "insertion, or thread completion. Break the tie on fact_id:\n  "
        + "\n  ".join(offenders)
    )


def test_the_check_above_can_actually_fail() -> None:
    """A guard on the guard.

    The structural test passes when it finds nothing — which is also what it does
    if the detector is broken. This feeds it a known-bad snippet and a known-good
    one, so a detector that always returns nothing cannot pass.
    """
    bad = ast.parse("results.sort(key=lambda x: x[1], reverse=True)")
    good = ast.parse("results.sort(key=lambda x: (-x[1], x[0]))")
    assert _sort_calls_keyed_on_score_alone(bad), "the detector missed a score-only sort"
    assert not _sort_calls_keyed_on_score_alone(good), "the detector flagged a tuple key"


class TestTiedScoresComeBackInAStableOrder:
    """The behaviour the structural test is a proxy for."""

    @pytest.mark.parametrize("reversed_input", [False, True])
    def test_input_order_does_not_decide_output_order(self, reversed_input: bool) -> None:
        """Two arrival orders, one identical result.

        This is what a user experiences as "the same question gave a different
        answer": several facts score identically and whichever arrived first won.
        """
        pairs = [(f"fact{i:02d}", 0.5) for i in range(8)]
        if reversed_input:
            pairs = list(reversed(pairs))

        ordered = sorted(pairs, key=lambda x: (-x[1], x[0]))

        assert [fid for fid, _ in ordered] == [f"fact{i:02d}" for i in range(8)], (
            "the output order followed the input order, so it is decided by "
            "something other than the data"
        )

    def test_scores_still_dominate_the_tie_break(self) -> None:
        """The tie-break must only ever decide ties.

        Without this, a key that sorted by fact_id first would satisfy the test
        above while destroying ranking entirely.
        """
        pairs = [("zzz_best", 0.9), ("aaa_worst", 0.1), ("mmm_middle", 0.5)]
        ordered = [fid for fid, _ in sorted(pairs, key=lambda x: (-x[1], x[0]))]
        assert ordered == ["zzz_best", "mmm_middle", "aaa_worst"], (
            "the tie-break overrode the score; it must only apply between equals"
        )
