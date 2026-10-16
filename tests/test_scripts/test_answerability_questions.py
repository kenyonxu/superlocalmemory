# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The generated answerability sets are frozen, large enough, and independent."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _gen():
    name = "answerability_questions"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "scripts" / "quality" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_frozen_files_are_what_the_generator_makes() -> None:
    gen = _gen()
    for half in gen.HALVES:
        for name, rows in gen.generate(half).items():
            assert _read(gen.fixture_path(half, name)) == json.loads(json.dumps(rows)), (half, name)


def test_each_half_is_large_enough_and_covers_every_kind() -> None:
    gen = _gen()
    for half in gen.HALVES:
        sets = gen.generate(half)
        assert len(sets["nonsense"]) >= 300 and len(sets["unanswerable"]) >= 80
        assert {r["category"] for r in sets["nonsense"]} == set(gen._NONSENSE)
        assert len(sets["real"]) >= 80


def test_the_halves_share_no_question_term_or_word_and_avoid_the_hand_sets() -> None:
    from superlocalmemory.retrieval.hub_baseline import probe_texts

    gen = _gen()
    dev, fresh = gen.generate("dev"), gen.generate("fresh")
    asked = {r["question"].lower() for name in ("nonsense", "real", "unanswerable")
             for r in _read(gen.FIXTURES / f"{name}.jsonl")}
    for name in ("nonsense", "unanswerable", "real"):
        d = {r["question"].lower() for r in dev[name]}
        f = {r["question"].lower() for r in fresh[name]}
        assert not d & f, name
        if name != "real":
            assert not (d | f) & asked
    assert not set(gen._TERMS["dev"]) & set(gen._TERMS["fresh"])
    assert not set(gen._words_for("dev")) & set(gen._words_for("fresh"))
    probes = {p.lower() for p in probe_texts()}
    assert not {r["question"].lower() for half in (dev, fresh) for r in half["nonsense"]} & probes


def test_no_nonsense_question_is_answerable_by_a_stored_note() -> None:
    gen = _gen()
    anchors = gen._anchor_terms()
    for half in gen.HALVES:
        for row in gen.nonsense(half):
            assert not gen.answerable_by_an_anchor(row["question"], anchors), row


def test_non_latin_nonsense_has_no_latin_letters() -> None:
    gen = _gen()
    for half in gen.HALVES:
        for row in gen.nonsense(half):
            if row["category"] == "non_latin":
                assert not any(c.isascii() and c.isalpha() for c in row["question"]), row
