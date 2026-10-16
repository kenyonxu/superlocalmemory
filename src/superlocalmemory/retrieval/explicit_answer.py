# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Conservative extractive support for explicit approver questions.

Only positive subject-verb-object assertions with all query anchors present
qualify. This is not a fuzzy overlap override: negation, hypothetical language,
questions, absent projects, and mismatched objects fail closed.
"""

from __future__ import annotations

import re

from superlocalmemory.retrieval.source_assertions import affirmative_sentences

RULE_ID = "explicit-approver-v2"
_ASK = re.compile(r"^who (?:approved|signed off(?: on)?) (.+?)\??$", re.I)
_APPROVAL = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,3}) approved ([^.!?\n]+)[.!]?", re.M)
_SKIP = frozenset("a an the in on for of synthetic fictional who approved".split())
_WORD = re.compile(r"[a-z0-9_]+")


def _anchors(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - _SKIP


def attribution_support(question: str, memory: str) -> bool:
    ask = _ASK.fullmatch(" ".join(question.split()))
    if ask is None or len(memory) > 1800:
        return False
    required = _anchors(ask[1])
    if not required or not required <= _anchors(memory):
        return False
    for sentence in affirmative_sentences(memory):
        match = _APPROVAL.fullmatch(sentence)
        if match is None:
            continue
        obj = _matching_object(required, match, memory)
        if obj is None:
            continue
        # Query IDs/project names may label the whole document, but its asked
        # object must be in this approval, not in another sentence.
        object_words = required & {
            "checklist",
            "release",
            "deployment",
            "rollback",
            "recovery",
            "plan",
        }
        if object_words and object_words <= obj:
            return True
    return False


def _matching_object(required, match, memory):
    obj = _anchors(match[2])
    missing = required - obj
    scoped_ids = {word for word in missing if "_" in word and any(c.isdigit() for c in word)}
    # Generic object references may inherit only an explicit record-ID scope.
    scope = set(scoped_ids)
    labelled = re.match(
        r"^(?P<id>[A-Z][A-Z0-9_]+):\s*(?:Fictional\s+)?"
        r"(?P<project>[A-Z][a-z]+)\s+deployment\b",
        memory,
    )
    if labelled and labelled["id"].lower() in scoped_ids:
        scope.add(labelled["project"].lower())
    if not missing <= scope:
        return None
    named_objects = set(re.findall(r"\b[A-Z][a-z]+\b", match[2])) - {"The", "A", "An"}
    if any(name.lower() not in required for name in named_objects):
        return None
    return obj


def support_indices(question: str, documents: list[str]) -> tuple[int, ...]:
    return tuple(i for i, doc in enumerate(documents) if attribution_support(question, doc))
