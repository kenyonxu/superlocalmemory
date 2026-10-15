# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The answer-or-abstain gate, measured on a ~2,000-memory synthetic store.

Slow: builds the store with the real embedding model and asks the frozen
question sets through the real recall pipeline with the real cross-encoder
(scripts/quality/answerability_eval.py, suite "frozen"): the hand-written sets
and the generated fresh half, which no recipe was fitted or thresholded on.

The gate as defined:

* every nonsense question abstains -- the hand-written set without exception;
  the fresh half except the questions named in ``OPEN_NONSENSE``, which the
  shipped recipe (answer-support-v2 at its strict threshold) still answers:
  a stored term plus one or two ordinary words. They stay named here until a
  later recipe abstains on them. A nonsense question answered that is NOT
  named fails the test.
* no real question is lost: every real question recall answered before the
  answer-support gate still has its memory in the top 5. The ones recall
  missed before are named in ``RETRIEVAL_MISSES_BEFORE``; they are retrieval
  misses (the memory is not among the candidates), not abstentions.

Set ``SLM_ANSWERABILITY_CACHE`` to a folder to reuse a built store between runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]

#: Real questions recall missed before the answer-support gate (recorded
#: baseline: 104/109 hand-written, 94/94 fresh). r-sem-1 is answered now.
RETRIEVAL_MISSES_BEFORE = frozenset({"r-sem-1", "r-sem-3", "r-sem-5", "r-sem-7", "r-sem-8"})
#: Fresh nonsense answer-support-v2 still answers (stored term plus one or two
#: ordinary words), from the 4.1.25 round-3 warm run. The Katakana phrase
#: (gf-n-non_latin-8) abstains since unspaced scripts are judged on pairs.
OPEN_NONSENSE = frozenset({
    "gf-n-term_plus_words-36",  # "exclusion GDPR"
    "gf-n-term_plus_words-48",  # "SLM_RECALL_NO_FLOOR seventieth"
    "gf-n-term_plus_words-50",  # "denigrate rrf_k"
    "gf-n-term_plus_words-54",  # "FSRS nuclei"
})


@pytest.fixture(scope="module")
def profile(tmp_path_factory):
    """One harness run in its own process: the suite's session fixtures replace
    the cross-encoder with a stub, and this gate is about recall as it ships."""
    import json
    import subprocess

    work = tmp_path_factory.mktemp("answerability")
    rows_path = work / "rows.jsonl"
    env = {k: v for k, v in os.environ.items() if k != "SLM_TEST_ISOLATION"}
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env["PYTHONPATH"] = str(ROOT / "src")
    cache = os.environ.get("SLM_ANSWERABILITY_CACHE")
    cmd = [sys.executable, str(ROOT / "scripts" / "quality" / "answerability_eval.py"),
           "--work", str(work), "--profiles", "a", "--suite", "frozen",
           "--rows", str(rows_path), *(["--cache", cache] if cache else [])]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=7200,
                          check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return {"rows": rows}


def _rows(profile, kind):
    return [r for r in profile["rows"] if r["set"] == kind]


def test_every_hand_written_nonsense_question_abstains(profile) -> None:
    answered = [r["qid"] for r in _rows(profile, "nonsense")
                if r["source"] == "nonsense" and not r["abstained"]]
    assert answered == []


def test_no_fresh_nonsense_question_answers_beyond_the_named_open_ones(profile) -> None:
    answered = {r["qid"] for r in _rows(profile, "nonsense")
                if r["source"] != "nonsense" and not r["abstained"]}
    assert answered <= OPEN_NONSENSE, sorted(answered - OPEN_NONSENSE)
    fresh = [r for r in _rows(profile, "nonsense") if r["source"] != "nonsense"]
    assert len(fresh) - len(answered) >= 0.98 * len(fresh)


def test_no_real_question_recall_answered_before_is_lost(profile) -> None:
    missed = {r["qid"] for r in _rows(profile, "real")
              if r["rank"] is None or r["rank"] > 5}
    assert missed <= RETRIEVAL_MISSES_BEFORE, sorted(missed - RETRIEVAL_MISSES_BEFORE)
