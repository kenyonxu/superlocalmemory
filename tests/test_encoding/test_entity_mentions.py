# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Mode A names what a developer's notes name, and not the words around them.

Measured on the author's store (4.1.25 local test): "The", "Our", "My", "Wi",
"Fi", "Dr", "Decision" were saved as entities, while "SuperLocalMemory",
"AgentAssert", "pgbouncer", "vitamin D" and "billing service" were not. Every
write path had its own copy of the same two regexes; they now share one.
"""

from __future__ import annotations

import time

import pytest

from superlocalmemory.core.queryable_fact import queryable_fact
from superlocalmemory.encoding.entity_mentions import extract_mentions
from superlocalmemory.encoding.fact_extractor import _extract_entities
from superlocalmemory.retrieval.entity_channel import extract_query_entities

JUNK = {"The", "Our", "My", "Wi", "Fi", "Dr", "Decision", "Status", "Rule", "Friday",
        "October", "Sunday"}


@pytest.mark.parametrize(("text", "wanted"), [
    ("The router died. Our Wi-Fi is down. My doctor, Dr. Iyer, called.", {"Wi-Fi", "Iyer"}),
    ("We shipped SuperLocalMemory and AgentAssert, then AgentAssay.",
     {"SuperLocalMemory", "AgentAssert", "AgentAssay"}),
    ("pgbouncer sits in front of Postgres.", {"pgbouncer", "Postgres"}),
    ("I take vitamin D every morning.", {"vitamin D"}),
    ("The billing service timed out again.", {"billing service"}),
    ("SLM 4.1.25 fixed it; Python 3.12 did not.", {"SLM 4.1.25", "SLM", "Python 3.12", "Python"}),
    ("Decision: move CI to GitHub Actions on Friday in October.", {"CI", "GitHub Actions"}),
    ("Status: OpenAI and iPhone support landed on macOS.", {"OpenAI", "iPhone", "macOS"}),
    ("Run llama3.2 with superpowers:brainstorming and the snake_case flag.",
     {"llama3.2", "superpowers:brainstorming", "snake_case"}),
])
def test_names_are_found_and_sentence_words_are_not(text, wanted) -> None:
    got = set(extract_mentions(text))
    assert wanted <= got, wanted - got
    assert not (got & JUNK), got & JUNK


def test_every_write_path_uses_the_same_extractor() -> None:
    text = "Our pgbouncer pool in front of SuperLocalMemory broke on Friday."
    expected = extract_mentions(text)
    assert _extract_entities(text) == expected
    fact = queryable_fact(text, profile_id="default", scope="personal", shared_with=None,
                          session_id="", observation_date="", created_at="")
    assert fact.entities == expected


def test_a_question_uses_the_same_rules_and_never_title_cases_words() -> None:
    assert extract_query_entities("zxqv plorth wimble") == []
    assert extract_query_entities("what is going on?") == []
    got = extract_query_entities("Did AgentAssert ship in SLM 4.1.25?")
    assert {"AgentAssert", "SLM 4.1.25", "SLM"} <= set(got)


def test_extraction_stays_cheap_on_every_write() -> None:
    text = ("Decision: we moved the billing service behind pgbouncer after the "
            "SuperLocalMemory 4.1.25 release; Varun and Dr. Iyer reviewed it with "
            "Claude Code on Friday. The Wi-Fi in the Pune office kept dropping. ") * 3
    extract_mentions(text)
    start = time.perf_counter()
    for _ in range(200):
        extract_mentions(text)
    per_fact_ms = (time.perf_counter() - start) * 1000 / 200
    assert per_fact_ms < 2.0, per_fact_ms
