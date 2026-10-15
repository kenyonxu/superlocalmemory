#!/usr/bin/env python3
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Generated question sets for the answerability harness, in two independent halves.

The hand-written sets in ``tests/fixtures/answerability`` (64 nonsense, 109
real, 32 no-answer) share one author's style, and a recipe fitted on them can
learn that style instead of the signal. These sets are generated, in two
halves that share no generator seed, no word pool, no template and no stored
term:

  dev    fitting and threshold selection (with the hand-written sets)
  fresh  a frozen final test, never used for either

Each half has:

  nonsense       word salads of 2-6 common words; one stored term (an id, a
                 version, an entity, a code identifier) plus ordinary words;
                 plausible-sounding but empty phrases ("release notes for the
                 moon"); the same with mixed case and punctuation; 1-2 word
                 made-up words; non-Latin made-up words
  unanswerable   attribute questions about stored people and projects whose
                 attribute was never stored ("Which university did Alexa
                 Morgan attend?")
  real           keyword-style questions made of an anchor memory's own content
                 words (it must come back in the top 5)

Word pools are split by a hash of the word, stored terms and templates are
listed per half, and every random draw comes from sha256 in counter mode, so
the files are the same on every machine. ``--write`` regenerates the frozen
fixture files; the test suite checks they still match.

    python scripts/quality/answerability_questions.py --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

GENERATOR_VERSION = "answerability-questions-v1"
HALVES = ("dev", "fresh")
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "answerability"
OUT = FIXTURES / "generated"
NONSENSE_PER_KIND = 55          # 6 kinds -> 330 per half
_WORD = re.compile(r"^[a-z]{3,10}$")


class _Stream:
    def __init__(self, label: str) -> None:
        self._label, self._n = label.encode(), 0

    def below(self, n: int) -> int:
        d = hashlib.sha256(self._label + self._n.to_bytes(8, "big")).digest()
        self._n += 1
        return int.from_bytes(d[:6], "big") % n

    def pick(self, seq):
        return seq[self.below(len(seq))]


def _half_of(text: str) -> str:
    h = hashlib.sha256(f"{GENERATOR_VERSION}:half:{text}".encode()).digest()[0]
    return HALVES[h % 2]


def common_words() -> list[str]:
    from importlib import resources

    text = resources.files("superlocalmemory.encoding").joinpath(
        "data", "common_english_words.txt").read_text(encoding="utf-8")
    return sorted(w for w in text.splitlines() if w and not w.startswith("#") and _WORD.match(w))


# Stored terms, split by half by hand so each half names different memories.
_TERMS = {
    "dev": ["v0.3.4", "4.1.25", "qjl_bits", "ensure_daemon", "parse_window", "OPS-4471",
            "asdk_app_6ac9e8f2b71d40c3", "AUDIT-0005", "Kestrel", "Orchid", "Lumen",
            "Priya Sen", "Nadia Okafor", "HTTP 421", "CSLS", "SQLite", "Node 22"],
    "fresh": ["4.1.24", "rrf_k", "warmup_sync", "CANONICAL_RECALL_LIMIT", "SLM_RECALL_NO_FLOOR",
              "req_01J9ZK4M8Q2X7V3N5B6C", "ORD-2026-000917", "LIC-7Q2M-88XT-0PZK", "9d4e1b7",
              "Meridian", "Juniper", "Tundra", "Atlas", "Quill", "Alexa Morgan",
              "Tomás Rivera", "Mei Tanaka", "FSRS", "GDPR", "PostgREST", "Python 3.14"],
}
_ABSURD = {
    "dev": ["moon", "volcano", "giraffe", "rainbow", "pancake", "glacier", "octopus",
            "lighthouse", "teacup", "comet", "cactus", "snowman"],
    "fresh": ["nebula", "walrus", "trampoline", "lollipop", "iceberg", "kangaroo",
              "carousel", "meteor", "marshmallow", "sandcastle", "dragonfly", "accordion"],
}
_EMPTY_TEMPLATES = {
    "dev": ["release notes for the {x}", "what did the {x} committee decide",
            "status of the {x} migration", "who signed off the {x} rollout",
            "when does the {x} ship", "rollback plan for the {x}"],
    "fresh": ["changelog of the {x}", "why was the {x} deprecated",
              "latest benchmark for the {x}", "who owns the {x} backlog",
              "deployment window for the {x}", "postmortem of the {x} outage"],
}
_SYLLABLES = {
    "dev": ["bra", "zon", "kli", "mup", "tor", "fen", "qua", "vix", "dro", "pel"],
    "fresh": ["gli", "sto", "wum", "rax", "nib", "jor", "pha", "kev", "lut", "zib"],
}
_SCRIPTS = {
    "dev": [(0x4E00, 0x4FFF), (0x0410, 0x044F)],          # CJK, Cyrillic
    "fresh": [(0x0905, 0x0939), (0x0627, 0x064A), (0x30A1, 0x30FA)],  # Devanagari, Arabic, Katakana
}

_PEOPLE = ["Priya Sen", "Alexa Morgan", "Alex Morgan", "Morgan Patel", "Joan Smith",
           "John Smith", "Nadia Okafor", "Tomás Rivera", "Mei Tanaka", "Oskar Lindqvist",
           "Alice Chen"]
_PROJECTS = ["Kestrel", "Orchid", "Meridian", "Juniper", "Tundra", "Atlas", "Cedar", "Lumen",
             "Orbit", "Quill"]
_PERSON_Q = {
    "dev": ["Which university did {p} attend?", "When is {p}'s birthday?",
            "What is {p}'s phone number?", "What is {p}'s favourite colour?",
            "Who is {p} married to?"],
    "fresh": ["Where did {p} grow up?", "What was {p}'s first job?",
              "Which car does {p} drive?", "What is {p}'s shoe size?",
              "Which hobby does {p} have?"],
}
_PROJECT_Q = {
    "dev": ["What colour is the {j} logo?", "Who is the CEO of {j}?",
            "How many paying customers does {j} have?", "Where was the {j} launch party?"],
    "fresh": ["What is the {j} mascot called?", "Which office address does {j} use?",
              "What is the {j} legal registration number?", "Who designed the {j} website?"],
}


def _words_for(half: str) -> list[str]:
    return [w for w in common_words() if _half_of(w) == half]


def _salad(s: _Stream, words: list[str], n: int) -> str:
    return " ".join(s.pick(words) for _ in range(n))


def _gen_salad(s, half, words):
    return _salad(s, words, 2 + s.below(5))


def _gen_term_plus_words(s, half, words):
    parts = [_salad(s, words, 1 + s.below(3)), s.pick(_TERMS[half])]
    return " ".join(parts if s.below(2) else parts[::-1])


def _gen_empty(s, half, words):
    return s.pick(_EMPTY_TEMPLATES[half]).format(x=s.pick(_ABSURD[half]))


def _gen_mixed(s, half, words):
    base = _gen_term_plus_words(s, half, words) if s.below(2) else _salad(s, words, 2 + s.below(3))
    out = "".join(c.upper() if s.below(3) == 0 else c for c in base)
    return out + s.pick(["?!", "...", "!!", " ??", ";", " -- ok?"])


def _pseudo_word(s: _Stream, half: str) -> str:
    return "".join(s.pick(_SYLLABLES[half]) for _ in range(2 + s.below(2)))


def _gen_short(s, half, words):
    return " ".join(_pseudo_word(s, half) for _ in range(1 + s.below(2)))


def _gen_non_latin(s, half, words):
    lo, hi = s.pick(_SCRIPTS[half])
    return " ".join("".join(chr(lo + s.below(hi - lo)) for _ in range(2 + s.below(4)))
                    for _ in range(1 + s.below(3)))


_NONSENSE: dict[str, Callable] = {
    "word_salad": _gen_salad, "term_plus_words": _gen_term_plus_words,
    "empty_phrase": _gen_empty, "mixed_case": _gen_mixed, "short_made_up": _gen_short,
    "non_latin": _gen_non_latin,
}


_TERM_RE = re.compile(r"\w+(?:[._:/-]\w+)*", re.UNICODE)


def _terms(text: str) -> set[str]:
    return {t for t in _TERM_RE.findall(text.lower()) if t not in _STOP}


def _anchor_terms() -> list[set[str]]:
    return [_terms(json.loads(line)["content"]) for line in
            (FIXTURES / "anchors.jsonl").read_text(encoding="utf-8").splitlines() if line]


def answerable_by_an_anchor(question: str, anchors: list[set[str]]) -> bool:
    """Two or more of the question's words in one hand-written memory: the draw
    may have made a real question by chance, so it is not used as nonsense."""
    q = _terms(question)
    return any(len(q & a) >= 2 for a in anchors)


def nonsense(half: str) -> list[dict]:
    words = _words_for(half)
    anchors = _anchor_terms()
    out, seen = [], set()
    for kind, gen in _NONSENSE.items():
        s = _Stream(f"{GENERATOR_VERSION}:{half}:{kind}")
        n = 0
        while n < NONSENSE_PER_KIND:
            q = gen(s, half, words)
            if q.lower() in seen or answerable_by_an_anchor(q, anchors):
                continue
            seen.add(q.lower())
            n += 1
            out.append({"qid": f"g{half[0]}-n-{kind}-{n}", "category": kind,
                        "expected": "abstain", "question": q})
    return out


def _hand_written() -> set[str]:
    return {json.loads(line)["question"].lower() for name in ("nonsense", "real", "unanswerable")
            for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if line}


def unanswerable(half: str) -> list[dict]:
    """Attribute questions about stored people and projects; one the hand-written
    set already asks is left out."""
    hand = _hand_written()
    asks = [("person", t.format(p=p)) for p in _PEOPLE for t in _PERSON_Q[half]]
    asks += [("project", t.format(j=j)) for j in _PROJECTS for t in _PROJECT_Q[half]]
    return [{"qid": f"g{half[0]}-u-{kind}-{i}", "category": "entity_attribute",
             "expected": "abstain", "question": q}
            for i, (kind, q) in enumerate(asks) if q.lower() not in hand]


_STOP = frozenset("""a an the is are was were be been being am have has had do does did will
would shall should may might must can could of in to for with on at from by as into about and
but or not so if then than that this these those it its i me my we our us you your he him his
she her they them their what which who whom whose when where why how there here any some all
much many after before since until now ever only also every each one two no yes""".split())
_REAL_FRAMES = {"dev": ["{t}", "{t}?", "what about {t}", "anything on {t}"],
                "fresh": ["{t}", "tell me {t}", "{t} - details?", "notes on {t}"]}
#: Hand-written anchors written in another language, or pairs that answer one another.
_PARTNERS = {"pp-dash-port-a": ["pp-dash-port-b"], "pp-dash-port-b": ["pp-dash-port-a"],
             "pp-retention-a": ["pp-retention-b"], "pp-retention-b": ["pp-retention-a"],
             "pp-coffee-a": ["pp-coffee-b"], "pp-coffee-b": ["pp-coffee-a"]}


def real(half: str) -> list[dict]:
    """Keyword questions made of an anchor's own content words: 2-4 of them,
    in order, chosen differently per half."""
    out = []
    term_re = re.compile(r"\w+(?:[._:/-]\w+)*", re.UNICODE)
    for line in (FIXTURES / "anchors.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        terms = [t for t in term_re.findall(row["content"]) if t.lower() not in _STOP
                 and len(t) >= 3 and t.isascii()]
        if len(terms) < 3:
            continue
        s = _Stream(f"{GENERATOR_VERSION}:{half}:real:{row['key']}")
        k = min(len(terms), 2 + s.below(3))
        start = s.below(len(terms) - k + 1)
        picked = terms[start:start + k]
        q = s.pick(_REAL_FRAMES[half]).format(t=" ".join(picked))
        out.append({"qid": f"g{half[0]}-r-{row['key']}", "category": "keyword_question",
                    "expected": [row["key"], *_PARTNERS.get(row["key"], [])], "question": q})
    return out


SETS = {"nonsense": nonsense, "unanswerable": unanswerable, "real": real}


def generate(half: str) -> dict[str, list[dict]]:
    return {name: fn(half) for name, fn in SETS.items()}


def fixture_path(half: str, name: str) -> Path:
    return OUT / f"{half}_{name}.jsonl"


def write() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for half in HALVES:
        for name, rows in generate(half).items():
            fixture_path(half, name).write_text(
                "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--write", action="store_true", help="regenerate the frozen fixture files")
    args = p.parse_args(argv)
    if args.write:
        write()
    for half in HALVES:
        print(half, {name: len(rows) for name, rows in generate(half).items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
