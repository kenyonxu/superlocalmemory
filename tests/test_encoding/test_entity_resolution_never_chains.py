# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Two different things are never made one entity, and merges never chain.

Measured on the author's 26k-fact store (4.1.25 audit): 71% of the entity
links on freshly written notes were wrong. The resolver merged any name whose
whole-string Jaro-Winkler score reached 0.85 against a canonical name OR an
alias, and each such merge wrote an alias the next name could match: one
entity, "Lost", collected 145 aliases (Cost, Post, Last, PostgreSQL, ...), and
a question about "Plorth" landed on it through the alias "Plot".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from superlocalmemory.encoding.entity_resolver import EntityResolver
from superlocalmemory.retrieval.entity_channel import extract_query_entities
from superlocalmemory.storage import schema as real_schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import CanonicalEntity, EntityAlias

P = "default"

#: Each pair scored >= 0.85 and was auto-merged before 4.1.25 (ENGINE-AUDIT H1
#: and LOCAL-TEST finding 1).
DISTINCT_PAIRS = [
    ("AgentAssert", "AgentAssay"),
    ("SLM 4.1.24", "SLM 4.1.25"),
    ("Python 3.12", "Python 3.14"),
    ("Claude Code", "Claude Opus"),
    ("Gemini 3.1 Pro", "Gemini 3.8 Flash"),
    ("John Smith", "John Smyth"),
    ("Qualixar OS", "Qualixar"),
    ("Opus 4.5", "Opus 4.6"),
    ("Meena", "Menon"),
    ("Sanjay", "Sajal"),
    ("Wants", "Warren"),
    ("Rao Builders", "Built"),
    ("Warranty", "Wants"),
]

#: Names the old alias set of "Lost" held on the author's store.
LOST_ALIASES = ["Cost", "Post", "Last", "Logs", "Plot", "PostgreSQL", "Podcasts", "Vlogs",
                "Astro", "BLAST", "MONSTER"]


@pytest.fixture()
def db(tmp_path: Path) -> DatabaseManager:
    mgr = DatabaseManager(tmp_path / "memory.db")
    mgr.initialize(real_schema)
    return mgr


@pytest.fixture()
def chained(db: DatabaseManager) -> DatabaseManager:
    """A store holding the old chained alias set of "Lost"."""
    db.store_entity(CanonicalEntity(entity_id="e_lost", profile_id=P, canonical_name="Lost"))
    db.store_alias(EntityAlias(entity_id="e_lost", alias="Lost", source="canonical"), P)
    for alias in LOST_ALIASES:
        db.store_alias(EntityAlias(entity_id="e_lost", alias=alias, confidence=0.9,
                                   source="jaro_winkler"), P)
    return db


@pytest.mark.parametrize(("first", "second"), DISTINCT_PAIRS)
def test_different_names_never_become_one_entity(db, first, second) -> None:
    resolver = EntityResolver(db, llm=None)
    a = resolver.resolve([first], P)[first]
    b = resolver.resolve([second], P)[second]
    assert a != b, f"{first!r} and {second!r} were merged"


def test_three_near_names_do_not_chain(db) -> None:
    """Kubernetes <- Kubernets is one typo; Kubernet is one typo from the
    alias but two from the entity's name, so it must not join."""
    resolver = EntityResolver(db, llm=None)
    root = resolver.resolve(["Kubernetes"], P)["Kubernetes"]
    assert resolver.resolve(["Kubernets"], P)["Kubernets"] == root
    assert resolver.resolve(["Kubernet"], P)["Kubernet"] != root


def test_a_long_chain_of_near_names_stays_apart(db) -> None:
    resolver = EntityResolver(db, llm=None)
    names = ["Wants", "Wan", "Warren", "Wang", "Watt", "Yang"]
    ids = [resolver.resolve([n], P)[n] for n in names]
    assert len(set(ids)) == len(names)


@pytest.mark.parametrize(("variant", "name"), [
    ("agentassert", "AgentAssert"),
    ("Wi-Fi", "WiFi"),
    ("Super Local Memory", "SuperLocalMemory"),
    ("Qualixar's", "Qualixar"),
    ("GPT-4", "GPT4"),
    ("Elodie Marchand", "Élodie Marchand"),
    ("Kubernets", "Kubernetes"),
    ("Alice Smitth", "Alice Smith"),
    ("Kestrel Harbor", "Kestrel Harbour"),
])
def test_true_variants_still_merge(db, variant, name) -> None:
    resolver = EntityResolver(db, llm=None)
    root = resolver.resolve([name], P)[name]
    assert resolver.resolve([variant], P)[variant] == root


def test_old_fuzzy_aliases_are_never_matched_again(chained) -> None:
    resolver = EntityResolver(chained, llm=None)
    out = resolver.resolve(["Plorth", "Postgres", "PostgreSQL", "Post"], P)
    assert "e_lost" not in out.values(), out


def test_a_merge_writes_an_alias_that_no_fuzzy_rule_will_use(db) -> None:
    resolver = EntityResolver(db, llm=None)
    root = resolver.resolve(["Kubernetes"], P)["Kubernetes"]
    resolver.resolve(["Kubernets"], P)
    sources = {dict(r)["alias"]: dict(r)["source"] for r in db.execute(
        "SELECT alias, source FROM entity_aliases WHERE entity_id = ?", (root,))}
    assert sources["Kubernets"] == "typo"


class _Model:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def generate(self, prompt: str, **_kw) -> str:
        self.prompts.append(prompt)
        n = prompt.count("\n- ") or 1
        return json.dumps({str(i + 1): self.answer for i in range(n)})


def test_mode_a_keeps_a_borderline_pair_apart(db) -> None:
    resolver = EntityResolver(db, llm=None)
    a = resolver.resolve(["PostgreSQL"], P)["PostgreSQL"]
    assert resolver.resolve(["Postgres"], P)["Postgres"] != a


def test_mode_b_merges_a_borderline_pair_only_when_the_model_says_so(db) -> None:
    yes = EntityResolver(db, llm=_Model(True))
    a = yes.resolve(["PostgreSQL"], P)["PostgreSQL"]
    assert yes.resolve(["Postgres"], P)["Postgres"] == a
    no = EntityResolver(db, llm=_Model(False))
    assert no.resolve(["John Smyth"], P)["John Smyth"] != no.resolve(
        ["John Smith"], P)["John Smith"]


def test_the_model_is_never_asked_about_names_whose_numbers_differ(db) -> None:
    model = _Model(True)
    resolver = EntityResolver(db, llm=model)
    a = resolver.resolve(["Python 3.12"], P)["Python 3.12"]
    assert resolver.resolve(["Python 3.14"], P)["Python 3.14"] != a
    b = resolver.resolve(["AgentAssert"], P)["AgentAssert"]
    assert resolver.resolve(["AgentAssay"], P)["AgentAssay"] != b
    assert model.prompts == []


def test_a_nonsense_question_finds_nothing_in_a_chained_store(chained) -> None:
    resolver = EntityResolver(chained, llm=None)
    names = extract_query_entities("zxqv plorth wimble")
    assert names == []
    assert resolver.lookup(["Zxqv", "Plorth", "Wimble"], P) == {}


def test_a_question_is_at_least_as_strict_as_a_save(db) -> None:
    resolver = EntityResolver(db, llm=None)
    resolver.resolve(["AgentAssert", "SLM 4.1.24", "Claude Code"], P)
    assert resolver.lookup(["AgentAssay", "SLM 4.1.25", "Claude Opus", "Post"], P) == {}
    found = resolver.lookup(["agentassert", "Agent Assert"], P)
    assert set(found) == {"agentassert", "Agent Assert"}


def test_old_aliases_answer_exact_lookups_until_the_repair(db) -> None:
    """Between upgrade and repair, genuine old aliases keep working: an old
    model alias and an old fuzzy alias the rules still accept (a typo)."""
    db.store_entity(CanonicalEntity(entity_id="e_r", profile_id=P, canonical_name="Robert"))
    db.store_alias(EntityAlias(entity_id="e_r", alias="Bob", source="llm"), P)
    db.store_entity(CanonicalEntity(entity_id="e_k", profile_id=P, canonical_name="Kubernetes"))
    db.store_alias(EntityAlias(entity_id="e_k", alias="Kubernets", source="jaro_winkler"), P)
    resolver = EntityResolver(db, llm=None)
    assert resolver.lookup(["Bob", "Kubernets"], P) == {"Bob": "e_r", "Kubernets": "e_k"}
    assert resolver.resolve(["Bob"], P)["Bob"] == "e_r"


def test_a_question_uses_no_old_fuzzy_alias_even_spelled_exactly(chained) -> None:
    resolver = EntityResolver(chained, llm=None)
    assert resolver.lookup(["Plot", "PostgreSQL", "Podcasts"], P) == {}
    assert resolver.lookup(["Lost"], P) == {"Lost": "e_lost"}


@pytest.fixture()
def people(chained) -> EntityResolver:
    for eid, name in (("e_alice", "Alice"), ("e_priya", "Priya"), ("e_post", "Post"),
                      ("e_plot", "Plot")):
        chained.store_entity(CanonicalEntity(entity_id=eid, profile_id=P, canonical_name=name))
    return EntityResolver(chained, llm=None)


def _plain(resolver: EntityResolver, question: str) -> dict[str, str]:
    from superlocalmemory.retrieval.entity_channel import query_plain_words

    return resolver.lookup_plain_words(query_plain_words(question), P)


def test_a_lower_case_question_still_reaches_a_person(people) -> None:
    assert set(_plain(people, "what did alice say").values()) == {"e_alice"}
    assert set(_plain(people, "when is priya's birthday").values()) == {"e_priya"}


def test_everyday_words_in_lower_case_reach_nothing(people) -> None:
    for question in ("what was lost", "check the post", "the plot thickens",
                     "zxqv plorth wimble"):
        assert _plain(people, question) == {}, question


def test_an_everyday_word_still_reaches_an_entity_typed_as_a_person(chained) -> None:
    chained.store_entity(CanonicalEntity(entity_id="e_mark", profile_id=P,
                                         canonical_name="Mark", entity_type="person"))
    resolver = EntityResolver(chained, llm=None)
    assert _plain(resolver, "what did mark say") == {"mark": "e_mark"}


def test_the_entity_channel_answers_lower_case_questions(chained) -> None:
    from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord

    resolver = EntityResolver(chained, llm=None)
    chained.store_memory(MemoryRecord(memory_id="m0", profile_id=P, content="notes"))
    for fact_id, text in (("f_alice", "Alice said the launch moves to Monday."),
                          ("f_priya", "Priya's birthday is on 2 October."),
                          ("f_lost", "Lost the spare key near the post office.")):
        names = ["Alice"] if "Alice" in text else ["Priya"] if "Priya" in text else ["Lost"]
        links = list(resolver.resolve(names, P).values())
        chained.store_fact(AtomicFact(fact_id=fact_id, memory_id="m0", profile_id=P,
                                      content=text, entities=names, canonical_entities=links))
    channel = EntityGraphChannel(chained, resolver)
    assert [f for f, _ in channel.search("what did alice say", P)] == ["f_alice"]
    assert [f for f, _ in channel.search("when is priya's birthday", P)] == ["f_priya"]
    assert channel.search("i lost my post", P) == []
    assert channel.search("zxqv plorth wimble", P) == []


def test_confirmation_turns_reasoning_off_and_works_without_that_option() -> None:
    from superlocalmemory.encoding.entity_confirm import confirm_pairs

    class Thinking:
        def __init__(self) -> None:
            self.think = None

        def generate(self, prompt, system="", max_tokens=0, temperature=0.0, think=None):
            self.think = think
            return '{"1": true, "2": false}'

    class Plain:
        def generate(self, prompt, system="", max_tokens=0, temperature=0.0):
            return '```json {"1": "yes", "2": "no"} ```'

    thinking = Thinking()
    pairs = [("Postgres", "PostgreSQL"), ("Jon", "Jan")]
    assert confirm_pairs(thinking, pairs) == [True, False] and thinking.think is False
    assert confirm_pairs(Plain(), pairs) == [True, False]
