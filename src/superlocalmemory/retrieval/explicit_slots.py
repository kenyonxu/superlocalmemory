# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Narrow extractive slot proofs; no fuzzy entity/role equivalence is inferred."""

from __future__ import annotations

import re

from superlocalmemory.retrieval.source_assertions import affirmative_sentences

RULE_ID = "requested-slot-v2"
_STOP = frozenset("what which who is are the a an of for to be do as with by in on from "
                  "used called named employee person".split())
_UNCERTAIN = re.compile(
    r"\b(not|never|no|unknown|if|unless|may|might|said|claims?|whether)\b", re.I
)
_UNKNOWN = re.compile(
    r"(?:has|have) not been (?:recorded|saved|provided)|\bunknown\b|not (?:known|available)", re.I
)
_USAGE = re.compile(r"^what is (.+?) used for\??$", re.I)
_PERSON = re.compile(r"^(?:who|which (?:employee|person)) is (.+?)\??$", re.I)
_ROLE = re.compile(r"^what is (?:the )?ceo of (.+?) (?:called|named)\??$", re.I)


def _words(text):
    return set(re.findall(r"[a-z0-9_]+", text.lower())) - _STOP


def supports_slot(question: str, memory: str) -> bool:
    usage = _USAGE.fullmatch(question.strip())
    person = _PERSON.fullmatch(question.strip())
    for sentence in affirmative_sentences(memory):
        if usage:
            purpose = re.fullmatch(
                rf"\s*{re.escape(usage[1])}\s+"
                r"(?:exposes|provides|converts|serves|is used (?:for|to))\s+(?P<value>.+)",
                sentence, re.I,
            )
            if purpose and _words(purpose["value"]):
                return True
        if person and _words(person[1]) <= _words(sentence):
            # Require a named subject and the exact requested predicate.
            if re.fullmatch(
                rf"\s*[A-Z][a-z]+(?: [A-Z][a-z]+)+ is {re.escape(person[1])}\s*", sentence
            ):
                return True
    return False


def rejects_slot(question: str, documents: list[str]) -> bool:
    required = _words(question)
    sentences = [s for doc in documents for s in re.split(r"[.!?\n]", doc)]
    scoped = [s for s in sentences if required and required <= _words(s)]
    value_question = re.match(r"^(?:what (?:is|are)|how much)\b", question, re.I)
    if value_question and scoped and any(_UNKNOWN.search(s) for s in scoped):
        # A stated value in another matching assertion takes precedence over
        # a historical unknown. Never turn an unrelated sentence into a value.
        known = any(not _UNCERTAIN.search(s) and not _UNKNOWN.search(s) for s in scoped)
        if not known:
            return True
    role = _ROLE.fullmatch(question.strip())
    if role:
        relevant = [doc for doc in documents if re.search(rf"\b{re.escape(role[1])}\b", doc, re.I)]
        return bool(relevant) and all(
            re.fullmatch(
                rf"\s*(?:The )?{re.escape(role[1])} project lead is "
                r"[A-Z][a-z]+(?: [A-Z][a-z]+)+[.]?\s*",
                doc,
            )
            is not None
            for doc in relevant
        )
    return False
