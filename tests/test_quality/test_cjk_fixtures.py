# tests/test_quality/test_cjk_fixtures.py
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Schema and quality gates for the hand-written CJK answerability anchors.

The CJK anchors must look like a year of a Chinese agent's real memory and
must be answerable the way the EN anchors are: every row carries concrete,
question-targetable detail (numbers, names, unique terms) at the bar of the
EN ``ensure_daemon ... 12 seconds`` note. These tests pin the schema
(``{key, content, kind, project}``, unique ``cjk-`` keys, >=90 rows), the kind
vocabulary (exactly the EN anchor kinds), the language mix (Chinese prose
rows carry Han; the foreign-language notes stay non-Chinese), the Latin
identifier minority that id/code/version questions point at, the paraphrase
pairs, and the five hard acceptance probes that Task 3 must be able to ask
about (plan: 2026-10-19-cjk-answer-support-calibration).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
CJK_FIXTURES = _REPO / "tests" / "fixtures" / "answerability_cjk"
EN_FIXTURES = _REPO / "tests" / "fixtures" / "answerability"

#: The five hard acceptance probes from the plan's Task 6 gate. Each must
#: have at least one anchor home carrying the exact term, so Task 3 can
#: write exact/paraphrase questions for it.
HARD_PROBES = ("九间房缺口", "阿克希利", "M3d 基线", "maid-veil", "面纱", "骏骏")

_HAN = re.compile(r"[\u4e00-\u9fff]")
#: A Latin identifier long enough to be a question target (function name,
#: id, version) rather than an incidental acronym.
_LATIN_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{5,}")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def cjk_anchors() -> list[dict]:
    return _rows(CJK_FIXTURES / "anchors.jsonl")


def test_cjk_anchor_count_reaches_corpus_scale():
    # ~100 rows: the suite must be big enough to interleave through ~1900
    # filler rows the way the EN anchors do.
    assert len(cjk_anchors()) >= 90


def test_cjk_anchor_schema_and_unique_keys():
    rows = cjk_anchors()
    keys = [row["key"] for row in rows]
    contents = [row["content"] for row in rows]
    assert all(key.startswith("cjk-") for key in keys)
    assert len(set(keys)) == len(keys)
    assert len(set(contents)) == len(contents)
    for row in rows:
        assert set(row) == {"key", "content", "kind", "project"}
        assert isinstance(row["content"], str) and row["content"].strip()
        assert len(row["content"]) >= 10
        assert isinstance(row["project"], str) and row["project"].strip()


def test_cjk_kind_vocabulary_is_exactly_the_en_anchor_kinds():
    en_kinds = {row["kind"] for row in _rows(EN_FIXTURES / "anchors.jsonl")}
    cjk_kinds = {row["kind"] for row in cjk_anchors()}
    assert cjk_kinds == en_kinds


def test_chinese_anchors_carry_han_prose():
    # Everything except the foreign-language notes is Chinese prose.
    for row in cjk_anchors():
        if row["kind"] != "note":
            assert _HAN.search(row["content"]), row["key"]


def test_foreign_notes_stay_non_chinese():
    # The non_chinese question mirror points at Latin-script notes, the way
    # the EN non_english questions point at non-English notes.
    notes = [row for row in cjk_anchors() if row["kind"] == "note"]
    assert len(notes) >= 5
    for row in notes:
        assert not _HAN.search(row["content"]), row["key"]


def test_latin_identifier_anchors_are_a_supported_minority():
    # id/code/version questions need exact Latin tokens to point at, but the
    # store must stay a Chinese store: Latin-bearing rows stay a minority.
    rows = cjk_anchors()
    latin_rows = [row for row in rows if _LATIN_ID.search(row["content"])]
    assert len(latin_rows) >= 20
    assert len(latin_rows) < len(rows) / 2


def test_paraphrase_pairs_come_in_rows():
    # Same fact, two Chinese sayings, each its own row with its own key --
    # the cjk-pp-* block mirrors the EN pp-* pairs.
    pairs = [row for row in cjk_anchors() if row["key"].startswith("cjk-pp-")]
    assert len(pairs) >= 6
    assert len(pairs) % 2 == 0


def test_every_anchor_carries_questionable_detail():
    # The EN questionability bar: each row must hold at least one Han name
    # or term plus a digit, or a long Latin identifier -- something a
    # question can point at that filler rows do not carry.
    for row in cjk_anchors():
        has_digit = any(ch.isdigit() for ch in row["content"])
        has_latin_id = bool(_LATIN_ID.search(row["content"]))
        assert has_digit or has_latin_id, row["key"]


@pytest.mark.parametrize("probe", HARD_PROBES)
def test_hard_probes_have_anchor_homes(probe):
    homes = [row["key"] for row in cjk_anchors() if probe in row["content"]]
    assert homes, f"probe {probe!r} has no anchor home"


def test_maid_veil_probe_is_one_home_with_both_terms():
    # The acceptance probe is the pair "面纱 maid-veil": one world-memory
    # anchor must carry the Chinese term and the asset id together.
    homes = [row for row in cjk_anchors()
             if "maid-veil" in row["content"] and "面纱" in row["content"]]
    assert len(homes) == 1
