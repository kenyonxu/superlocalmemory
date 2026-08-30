# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""What the miner finds is injected as "default to these when generating code".

The technology keywords were matched as substrings, and the short ones live
inside ordinary English: "going" contains "go", "reaction" contains "react",
"digital" contains "git", "javascript" contains "java". One real sentence —
"the digital transformation is going well, the reaction was mixed" — produced
Git, Go and React, and those rows feed the prompt an assistant is handed on
every turn.

Steering an assistant's technology choices off a substring collision is not a
cosmetic defect: the user never said any of those words.
"""

from __future__ import annotations

import pytest

from superlocalmemory.learning.pattern_miner import _TECH_KEYWORDS, _matcher


def _found(text: str) -> set[str]:
    return {label for kw, label in _TECH_KEYWORDS.items() if _matcher(kw).search(text)}


@pytest.mark.parametrize(
    "text",
    [
        "the digital transformation is going well, the reaction was mixed",
        "the meeting was productive and the outcome was good",
        "I was going to mention the reaction to the digital rollout",
        "a pipeline of ongoing work",
    ],
)
def test_ordinary_english_names_no_technology(text: str) -> None:
    assert _found(text) == set(), f"{text!r} was read as naming {_found(text)}"


def test_a_longer_name_does_not_also_match_the_shorter_one() -> None:
    assert _found("javascript is fine") == {"JavaScript"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We use Go and React", {"Go", "React"}),
        ("I prefer Java over Kotlin", {"Java"}),
        ("the repo is on Git", {"Git"}),
        ("deployed to AWS", {"AWS"}),
    ],
)
def test_a_real_mention_is_still_found(text: str, expected: set) -> None:
    assert expected <= _found(text), (
        f"{text!r} named {expected} and the miner found {_found(text)}"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Whole-word matching cost these when it landed: there is no boundary
        # inside "golang" or "nodejs", and people write them constantly.
        ("rewriting the API in golang this quarter", "Go"),
        ("migrating the workers to nodejs 22", "Node.js"),
        ("the frontend is reactjs with hooks", "React"),
        ("we use node.js in the worker", "Node.js"),
        ("deployed on k8s", "Kubernetes"),
        ("the database is postgres", "PostgreSQL"),
    ],
)
def test_the_spellings_people_actually_type(text: str, expected: str) -> None:
    assert expected in _found(text), (
        f"{text!r} names {expected} and the miner found {_found(text)}"
    )


def test_every_punctuated_key_matches_its_own_name() -> None:
    """A boundary is only added where the keyword ends in a word character.

    The earlier version of this looped over three names and skipped any that
    were not in the table. None of them were, so it asserted nothing at all.
    This walks the table itself, so it cannot be empty.
    """
    punctuated = [k for k in _TECH_KEYWORDS if not k.isalnum()]
    assert punctuated, (
        "no keyword contains punctuation any more; if that is deliberate, "
        "delete this test rather than leaving it passing vacuously"
    )
    for keyword in punctuated:
        assert _matcher(keyword).search(f"we use {keyword} here"), (
            f"{keyword!r} no longer matches its own name"
        )
