# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The entity link repair dissolves chained aliases, relinks facts, and undoes.

The fixture is modelled on the author's store: "Lost" held 145 aliases the old
whole-string fuzzy merge had chained onto it (Cost, Post, Last, PostgreSQL,
Podcasts, ...), so facts about PostgreSQL and podcasts were linked to "Lost",
joined to each other by entity edges, and found by recall for "Lost".
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.encoding.entity_resolver import EntityResolver
from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.storage import entity_link_repair as elr
from superlocalmemory.storage import entity_link_repair_apply as ela
from superlocalmemory.storage import schema as real_schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import (
    AtomicFact,
    CanonicalEntity,
    EdgeType,
    EntityAlias,
    GraphEdge,
    MemoryRecord,
    TemporalEvent,
)

P = "default"
LOST_ALIASES = ["Cost", "Post", "Last", "Logs", "Plot", "PostgreSQL", "Podcasts", "Vlogs",
                "Astro", "BLAST", "MONSTER"]
FACTS = {  # fact -> (raw names, links as saved by the old resolver)
    "f_lost": (["Lost"], ["e_lost"]),
    "f_pg": (["PostgreSQL"], ["e_lost"]),
    "f_pod": (["Podcasts"], ["e_lost"]),
    "f_pg2": (["PostgreSQL", "Kubernetes"], ["e_lost", "e_kube"]),
    "f_kube": (["Kubernets"], ["e_kube"]),
    "f_meena": (["Meena"], ["e_menon"]),
    "f_menon": (["Menon"], ["e_menon"]),
    "f_priya": (["Priya"], ["e_alice"]),
    "f_alice": (["Alice Smith"], ["e_alice"]),
    "f_none": (["Zed"], []),
    # Saved by the old extractor: status words, an id, a path word, a stamp.
    "f_note": (["Final", "PASS", "LIVE", "IST", "b2f4b74fbbad434c", "Users", "Varun",
                "2026-09-14 00:55"], ["e_users", "e_live", "e_hex", "e_stamp"]),
}
NOTE = ("Final: PASS. LIVE at 2026-09-14 00:55 IST for b2f4b74fbbad434c, see "
        "/Users/yourusername/notes.md. Varun reviewed it.")
#: Junk the old extractor made entities of; e_orphan has no fact at all, and
#: e_summary has none either but a temporal event of an unrelinked fact names it.
JUNK = (("e_users", "Users"), ("e_live", "LIVE"), ("e_hex", "b2f4b74fbbad434c"),
        ("e_stamp", "2026-04-29 16:57"), ("e_orphan", "PASS"), ("e_summary", "VERIFIED"),
        ("e_speaker", "Ravi Kumar"))
EDGES = [("f_lost", "f_pg", "entity"), ("f_pg", "f_pod", "entity"),
         ("f_pg", "f_pg2", "entity"), ("f_meena", "f_menon", "temporal"),
         ("f_kube", "f_pg2", "entity"), ("f_lost", "f_pod", "semantic")]


def _build(path: Path) -> DatabaseManager:
    db = DatabaseManager(path)
    db.initialize(real_schema)
    for eid, name in (("e_lost", "Lost"), ("e_kube", "Kubernetes"), ("e_menon", "Menon"),
                      ("e_alice", "Alice Smith")):
        db.store_entity(CanonicalEntity(entity_id=eid, profile_id=P, canonical_name=name,
                                        fact_count=3))
        db.store_alias(EntityAlias(entity_id=eid, alias=name, source="canonical"), P)
    for alias in LOST_ALIASES:
        db.store_alias(EntityAlias(entity_id="e_lost", alias=alias, confidence=0.9,
                                   source="jaro_winkler"), P)
    db.store_alias(EntityAlias(entity_id="e_kube", alias="Kubernets", source="jaro_winkler"), P)
    db.store_alias(EntityAlias(entity_id="e_menon", alias="Meena", source="jaro_winkler"), P)
    db.store_alias(EntityAlias(entity_id="e_alice", alias="Priya", source="llm"), P)
    # A model's alias the rules leave to a model (one letter in a short word).
    db.store_alias(EntityAlias(entity_id="e_alice", alias="Alyce Smith", source="llm"), P)
    for eid, name in JUNK:
        db.store_entity(CanonicalEntity(entity_id=eid, profile_id=P, canonical_name=name))
        db.store_alias(EntityAlias(entity_id=eid, alias=name, source="canonical"), P)
    db.store_alias(EntityAlias(entity_id="e_stamp", alias="2026-04-29 16:58",
                               source="jaro_winkler"), P)
    # A profile derived from junk goes with it; an event of a fact the repair
    # does not relink keeps its entity.
    db.execute("INSERT INTO entity_profiles (profile_entry_id, entity_id, profile_id, "
               "knowledge_summary) VALUES ('ep1', 'e_orphan', ?, 'derived')", (P,))
    # A scene lists a junk entity beside a real one: only the junk id leaves.
    db.execute("INSERT INTO memory_scenes (scene_id, profile_id, theme, entity_ids_json) "
               "VALUES ('s1', ?, 'ops', '[\"e_live\", \"e_kube\"]')", (P,))
    db.store_memory(MemoryRecord(memory_id="m0", profile_id=P, content="notes"))
    for fact_id, (raw, links) in FACTS.items():
        text = NOTE if fact_id == "f_note" else f"note about {' and '.join(raw)}"
        db.store_fact(AtomicFact(fact_id=fact_id, memory_id="m0", profile_id=P,
                                 content=text, entities=raw,
                                 canonical_entities=links,
                                 observation_date="2026-10-01T00:00:00+00:00"))
    for i, (a, b, kind) in enumerate(EDGES):
        db.store_edge(GraphEdge(edge_id=f"edge{i}", profile_id=P, source_id=a, target_id=b,
                                edge_type=EdgeType(kind), weight=0.5))
    db.store_temporal_event(TemporalEvent(event_id="ev_pg", profile_id=P, entity_id="e_lost",
                                          fact_id="f_pg", observation_date="2026-10-01"))
    db.store_temporal_event(TemporalEvent(event_id="ev_keep", profile_id=P,
                                          entity_id="e_summary", fact_id="f_none",
                                          observation_date="2026-10-01"))
    db.store_temporal_event(TemporalEvent(event_id="ev_lost", profile_id=P,
                                          entity_id="e_lost", fact_id="f_lost",
                                          observation_date="2026-10-01"))
    return db


@pytest.fixture()
def db(tmp_path: Path) -> DatabaseManager:
    return _build(tmp_path / "memory.db")


def _state(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        tables = ("canonical_entities", "entity_aliases", "graph_edges", "temporal_events",
                  "fact_entity_associations", "entity_profiles", "memory_scenes")
        state = {t: sorted(map(repr, conn.execute(f"SELECT * FROM {t}").fetchall()))
                 for t in tables}
        return state | {"links": sorted(conn.execute(
            "SELECT fact_id, canonical_entities_json FROM atomic_facts").fetchall())}
    finally:
        conn.close()


def _plan(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        return elr.report(conn, elr.plan(conn))
    finally:
        conn.close()


def _links(db: DatabaseManager) -> dict[str, list[str]]:
    names = {r["entity_id"]: r["canonical_name"] for r in map(dict, db.execute(
        "SELECT entity_id, canonical_name FROM canonical_entities"))}
    return {r["fact_id"]: [names[i] for i in json.loads(r["canonical_entities_json"] or "[]")]
            for r in map(dict, db.execute("SELECT fact_id, canonical_entities_json "
                                          "FROM atomic_facts"))}


def test_the_dry_run_reports_and_changes_nothing(db) -> None:
    before = _state(db.db_path)
    report = _plan(db.db_path)
    assert _state(db.db_path) == before
    assert report["aliases_removed"] == 14          # Lost's 11, Meena, Priya, a stamp
    assert report["aliases_kept_relabelled"] == 2   # Kubernets (typo), Alyce Smith (model)
    assert report["facts_relinked"] == 6
    assert report["entities_to_create"] == 5        # PostgreSQL, Podcasts, Meena, Priya, Varun
    assert report["junk_entities_to_remove"] == 5   # Users, LIVE, the id, the stamp, PASS
    assert report["junk_entities_kept_referenced"] == 1  # VERIFIED: a temporal event
    assert report["edges_removed"] == 3
    assert report["temporal_events_repointed"] == 1
    assert report["most_aliases_removed"][0] == {"entity": "Lost", "aliases_removed": 11}


def test_apply_dissolves_the_chain_and_relinks_every_fact(db) -> None:
    result = ela.apply(db.db_path)
    assert result["changed"] and result["run_id"]
    links = _links(db)
    assert links["f_lost"] == ["Lost"]
    assert links["f_pg"] == ["PostgreSQL"]
    assert links["f_pod"] == ["Podcasts"]
    assert sorted(links["f_pg2"]) == ["Kubernetes", "PostgreSQL"]
    assert links["f_kube"] == ["Kubernetes"]
    assert links["f_meena"] == ["Meena"] and links["f_menon"] == ["Menon"]
    assert links["f_priya"] == ["Priya"]
    assert links["f_none"] == []
    assert links["f_note"] == ["Varun"]
    left = {dict(r)["canonical_name"] for r in db.execute(
        "SELECT canonical_name FROM canonical_entities WHERE entity_id IN "
        "('e_users','e_live','e_hex','e_stamp','e_orphan','e_summary','e_speaker')")}
    assert left == {"VERIFIED", "Ravi Kumar"}  # one still named elsewhere; a valid name
    assert not db.execute("SELECT 1 FROM entity_aliases WHERE entity_id = 'e_users'")
    assert not db.execute("SELECT 1 FROM entity_profiles WHERE entity_id = 'e_orphan'")
    scene = db.execute("SELECT entity_ids_json FROM memory_scenes WHERE scene_id = 's1'")
    assert json.loads(dict(scene[0])["entity_ids_json"]) == ["e_kube"]
    assert result["junk_entities_removed"] == 5
    aliases = {dict(r)["alias"]: dict(r)["source"] for r in db.execute(
        "SELECT alias, source FROM entity_aliases WHERE entity_id = 'e_lost'")}
    assert aliases == {"Lost": "canonical"}
    kept = {dict(r)["alias"]: dict(r)["source"] for r in db.execute(
        "SELECT alias, source FROM entity_aliases WHERE entity_id IN ('e_alice', 'e_kube')")}
    assert kept == {"Alice Smith": "canonical", "Alyce Smith": "llm_confirmed",
                    "Kubernetes": "canonical", "Kubernets": "typo"}
    edges = {dict(r)["edge_id"] for r in db.execute("SELECT edge_id FROM graph_edges")}
    assert edges == {"edge2", "edge4", "edge5"}  # shared PostgreSQL, Kubernetes; semantic
    event = dict(db.execute("SELECT entity_id FROM temporal_events "
                            "WHERE event_id = 'ev_pg'")[0])["entity_id"]
    assert event == db.get_entity_by_name("PostgreSQL", P).entity_id
    assert db.get_entity_by_name("Lost", P).fact_count == 1
    pairs = {(dict(r)["fact_id"], dict(r)["entity_id"]) for r in db.execute(
        "SELECT fact_id, entity_id FROM fact_entity_associations WHERE fact_id = 'f_pg'")}
    assert pairs == {("f_pg", db.get_entity_by_name("PostgreSQL", P).entity_id)}


def test_a_second_run_finds_nothing_to_do(db) -> None:
    ela.apply(db.db_path)
    after_first = _state(db.db_path)
    second = ela.apply(db.db_path)
    assert second["changed"] is False and second["facts_relinked"] == 0
    assert _state(db.db_path) == after_first


def test_undo_puts_back_exactly_what_was_there(db) -> None:
    before = _state(db.db_path)
    run = ela.apply(db.db_path)["run_id"]
    restored = ela.undo(db.db_path, run)
    assert restored["facts_restored"] == 6 and restored["created_entities_removed"] == 5
    assert restored["junk_entities_restored"] == 5
    assert _state(db.db_path) == before
    with pytest.raises(ela.UndoRefused):
        ela.undo(db.db_path, run)


def test_recall_finds_memories_through_the_entity_channel_after_repair(db) -> None:
    ela.apply(db.db_path)
    channel = EntityGraphChannel(db, EntityResolver(db, llm=None))
    pg = {fid for fid, _s in channel.search("What do we know about PostgreSQL?", P)}
    assert {"f_pg", "f_pg2"} <= pg
    lost = {fid for fid, _s in channel.search("What was Lost?", P)}
    # f_pod still sits one semantic edge from f_lost (kept: not entity-made).
    assert "f_lost" in lost and "f_pg" not in lost and "f_pg2" not in lost
    nonsense = channel.search("zxqv plorth wimble", P)
    assert nonsense == []
