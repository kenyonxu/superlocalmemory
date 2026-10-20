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

Task 3 adds the three question suites (``real`` / ``nonsense`` /
``unanswerable``) over these anchors: the verbatim row schemas, the category
distribution mirroring the EN hand-suite (``non_english`` renamed
``non_chinese`` for a Chinese store), qid uniqueness across all three files,
``expected`` keys that exist in the anchors and cover every one of them
(both ways -- the fit must see the whole store), the five hard probe
questions, and the nonsense/unanswerable textures (random Han gibberish,
Chinese word soup, unstored id-likes, exactly-one-shared-token questions,
absent-fact and near-miss questions that still touch the store).
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


# --- Task 3: the three question suites (real / nonsense / unanswerable) ----

QUESTION_SUITES = ("real", "nonsense", "unanswerable")

#: The one category rename the CJK mirror makes: an English store's
#: "other-language" category is non_english; a Chinese store's is
#: non_chinese (Latin-script questions against the non-Chinese notes).
CATEGORY_RENAME = {"non_english": "non_chinese"}

#: The five hard acceptance probes: required question tokens -> the anchor
#: home the probe question must point at. The maid-veil probe is the pair
#: 面纱 + maid-veil, matching the anchor-side pair probe above.
PROBE_QUESTIONS = (
    (("九间房缺口",), "cjk-world-nine-rooms-gap"),
    (("阿克希利",), "cjk-world-akexili"),
    (("M3d 基线",), "cjk-ver-m3d-baseline"),
    (("maid-veil", "面纱"), "cjk-world-maid-veil"),
    (("骏骏",), "cjk-name-junjun"),
)

_HAN_CHAR = re.compile(r"[\u4e00-\u9fff]")
#: Id-like Latin/digit runs (ids, hashes, versions, image tags).
_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{2,}")


def cjk_questions(name: str) -> list[dict]:
    return _rows(CJK_FIXTURES / f"{name}.jsonl")


def anchor_key_set() -> set[str]:
    return {row["key"] for row in cjk_anchors()}


def _shared_runs(question: str, content: str) -> set[str]:
    """Distinctive tokens shared by a question and one anchor row: bounded
    Latin/digit runs that appear verbatim, plus maximal common Han
    substrings of length >= 2 (阿克希利 counts as one shared token, not 3
    bigrams)."""
    shared = {m.group(0) for m in _LATIN_RUN.finditer(question)
              if m.group(0) in content}
    i, n = 0, len(question)
    while i < n:
        if not _HAN_CHAR.match(question[i]):
            i += 1
            continue
        best_end = None
        k = i + 2
        while k <= n and question[i:k] in content:
            best_end = k
            k += 1
        if best_end:
            shared.add(question[i:best_end])
            i = best_end
        else:
            i += 1
    return shared


def _max_shared_with_store(question: str) -> int:
    return max(len(_shared_runs(question, row["content"])) for row in cjk_anchors())


def test_cjk_real_suite_reaches_corpus_scale():
    # Mirror of the 109-row EN hand suite; >=100 rows of real questions.
    assert len(cjk_questions("real")) >= 100


def test_cjk_nonsense_and_unanswerable_reach_floors():
    # EN proportions are 64 nonsense / 32 unanswerable; the CJK floors sit
    # one row above them.
    assert len(cjk_questions("nonsense")) >= 65
    assert len(cjk_questions("unanswerable")) >= 33


def test_cjk_question_rows_match_the_verbatim_schemas():
    for name in QUESTION_SUITES:
        for row in cjk_questions(name):
            assert set(row) == {"qid", "category", "question", "expected"}, (name, row)
            assert isinstance(row["qid"], str) and row["qid"].strip()
            assert isinstance(row["question"], str) and len(row["question"].strip()) >= 2
    for row in cjk_questions("real"):
        expected = row["expected"]
        assert isinstance(expected, list) and expected, row["qid"]
        assert all(isinstance(key, str) and key for key in expected)
    for name in ("nonsense", "unanswerable"):
        assert all(row["expected"] == "abstain" for row in cjk_questions(name)), name


def test_cjk_categories_mirror_the_en_hand_suite():
    # Per file: same category tokens as the EN hand suite, except the one
    # mirrored rename.
    for name in QUESTION_SUITES:
        en_categories = {row["category"]
                         for row in _rows(EN_FIXTURES / f"{name}.jsonl")}
        mirrored = {CATEGORY_RENAME.get(cat, cat) for cat in en_categories}
        assert {row["category"] for row in cjk_questions(name)} == mirrored, name


def test_cjk_qids_unique_across_all_three_suites():
    qids = [row["qid"] for name in QUESTION_SUITES
            for row in cjk_questions(name)]
    assert len(set(qids)) == len(qids)


def test_cjk_question_texts_are_unique():
    questions = [row["question"] for name in QUESTION_SUITES
                 for row in cjk_questions(name)]
    assert len(set(questions)) == len(questions)


def test_cjk_real_expected_keys_exist_in_anchors():
    keys = anchor_key_set()
    for row in cjk_questions("real"):
        for key in row["expected"]:
            assert key in keys, (row["qid"], key)


def test_cjk_real_questions_cover_every_anchor():
    # Coverage both ways: every anchor is the expected home of at least one
    # real question, so the fit sees the whole store.
    probed = {key for row in cjk_questions("real") for key in row["expected"]}
    assert probed == anchor_key_set()


@pytest.mark.parametrize("tokens,home", PROBE_QUESTIONS,
                         ids=[home for _, home in PROBE_QUESTIONS])
def test_cjk_probe_terms_have_real_questions(tokens, home):
    # Each acceptance term appears in a real question that points at its
    # anchor home.
    hits = [row for row in cjk_questions("real")
            if home in row["expected"]
            and all(token in row["question"] for token in tokens)]
    assert hits, (tokens, home)


def test_cjk_non_chinese_questions_stay_non_chinese():
    # The non_chinese mirror of non_english: real questions in non-Chinese
    # scripts against the non-Chinese note anchors.
    rows = [row for row in cjk_questions("real")
            if row["category"] == "non_chinese"]
    assert len(rows) >= 5
    for row in rows:
        assert not _HAN.search(row["question"]), row["qid"]


def test_cjk_nonsense_random_letters_are_pure_han_gibberish():
    # 随机汉字串, mirroring the EN random letter soup: Han characters and
    # spaces only, no Latin, no digits.
    rows = [row for row in cjk_questions("nonsense")
            if row["category"] == "random_letters"]
    assert len(rows) >= 10
    for row in rows:
        assert len(_HAN.findall(row["question"])) >= 4, row["qid"]
        assert not re.search(r"[A-Za-z0-9]", row["question"]), row["qid"]


def test_cjk_nonsense_is_chinese_word_soup():
    # 中文词胡搭: in-vocabulary nonsense -- real Chinese words, absurd
    # combinations, no id-like Latin runs.
    rows = [row for row in cjk_questions("nonsense")
            if row["category"] == "in_vocabulary_nonsense"]
    assert len(rows) >= 10
    for row in rows:
        assert len(_HAN.findall(row["question"])) >= 6, row["qid"]
        assert not _LATIN_RUN.search(row["question"]), row["qid"]


def test_cjk_nonsense_ids_look_like_ids_and_are_unstored():
    # id_like_unstored: every row carries an id-like run, and its longest
    # such run appears in no anchor -- it genuinely was never stored.
    keys = anchor_key_set()
    assert keys  # anchors loaded
    rows = [row for row in cjk_questions("nonsense")
            if row["category"] == "id_like_unstored"]
    assert len(rows) >= 10
    for row in rows:
        runs = _LATIN_RUN.findall(row["question"])
        assert runs, row["qid"]
        longest = max(runs, key=len)
        assert all(longest not in anchor["content"] for anchor in cjk_anchors()), row["qid"]


def test_cjk_nonsense_shares_exactly_one_token_with_the_store():
    # 单词共享题: each question shares exactly one distinctive token with its
    # nearest anchor (max over the store == 1) and asks something else
    # entirely, so the gate must still abstain.
    rows = [row for row in cjk_questions("nonsense")
            if row["category"] == "shares_one_token"]
    assert len(rows) >= 10
    for row in rows:
        assert _max_shared_with_store(row["question"]) == 1, row["qid"]


def test_cjk_unanswerable_absent_rows_touch_stored_entities():
    # audit_absent: questions about stored entities whose asked-for fact was
    # never recorded -- they must touch the store (share a token) yet have
    # no home.
    rows = [row for row in cjk_questions("unanswerable")
            if row["category"] == "audit_absent"]
    assert len(rows) >= 10
    for row in rows:
        assert _max_shared_with_store(row["question"]) >= 1, row["qid"]


def test_cjk_unanswerable_near_miss_rows_are_adjacent_to_the_store():
    # near_miss: adjacent-but-absent facts -- the question sits right next
    # to a stored row (shares a token) but the answer does not exist.
    rows = [row for row in cjk_questions("unanswerable")
            if row["category"] == "near_miss"]
    assert len(rows) >= 5
    for row in rows:
        assert _max_shared_with_store(row["question"]) >= 1, row["qid"]
