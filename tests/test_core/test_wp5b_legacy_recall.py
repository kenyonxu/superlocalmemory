"""Old live-but-marked damage remains reviewable without entering new answers."""

import json

import pytest

from superlocalmemory.cli.fidelity_cmd import _connect_read_only, _to_review
from superlocalmemory.retrieval.source_fidelity_flags import flag_unverified
from superlocalmemory.storage.models import AtomicFact, MemoryRecord, RetrievalResult
from tests.test_core.test_source_fidelity_guard import _db

CASES = [
    (
        "Never publish without approval from the release owner.",
        "Publish with approval from the release owner",
        "negation_lost",
    ),
    (
        "Ship the iOS build before the Android build.",
        "Ship the Android build before the iOS build",
        "order_reversed",
    ),
    (
        "Previously the team used Jenkins, currently it uses GitHub Actions.",
        "The team uses Jenkins",
        "status_lost",
    ),
]


@pytest.mark.parametrize("source,damaged,reason", CASES)
def test_legacy_live_damage_is_hidden_but_stays_in_read_only_review(
    tmp_path, source, damaged, reason
):
    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=source)
    db.store_memory(record)
    fact = AtomicFact(memory_id=record.memory_id, content=damaged)
    db.store_fact(fact)
    original = AtomicFact(memory_id=record.memory_id, content=source)
    db.store_fact(original)
    results = [RetrievalResult(fact=f, score=1.0) for f in (fact, original)]
    assert [r.fact.fact_id for r in flag_unverified(results, db, "default")] == [original.fact_id]
    with _connect_read_only(str(db.db_path)) as conn:
        total, rows = _to_review(conn, "default", 50)
    assert total == 1 and reason in rows[0]["reasons"]
    assert (
        db.execute("SELECT quarantined FROM atomic_facts WHERE fact_id=?", (fact.fact_id,))[0][0]
        == 0
    )


def test_faithful_relative_date_uses_its_source_actor_and_observation(tmp_path):
    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(
        content="I met Priya yesterday.", speaker="Alex", session_date="2026-03-14"
    )
    db.store_memory(record)
    fact = AtomicFact(
        memory_id=record.memory_id,
        content="Alex met Priya on 2026-03-13.",
        observation_date=record.session_date,
    )
    db.store_fact(fact)
    results = [RetrievalResult(fact=fact, score=1.0)]
    assert flag_unverified(results, db, "default") == results


def test_shared_legacy_damage_cannot_escape_the_check(tmp_path):
    db = _db(tmp_path / "memory.db")
    source, damaged, _ = CASES[0]
    db.execute("INSERT INTO profiles (profile_id,name) VALUES ('peer','Peer')")
    record = MemoryRecord(content=source, profile_id="peer", scope="global")
    db.store_memory(record)
    fact = AtomicFact(
        memory_id=record.memory_id, content=damaged, profile_id="peer", scope="global"
    )
    db.store_fact(fact)
    results = [RetrievalResult(fact=fact, score=1.0)]
    assert flag_unverified(results, db, "default") == []


def test_ambiguous_speaker_candidate_is_quarantined_with_its_attribution_reason(tmp_path):
    from unittest.mock import Mock

    from superlocalmemory.core.config import EncodingConfig
    from superlocalmemory.core.source_fact_set import source_fact_set
    from superlocalmemory.encoding.fact_extractor import FactExtractor
    from superlocalmemory.storage.models import Mode

    db = _db(tmp_path / "memory.db")
    source = "[Alex]: I use Rust for payroll. [Priya]: I use Rust for payroll."
    record = MemoryRecord(content=source)
    db.store_memory(record)
    llm = Mock()
    llm.is_available.return_value = True
    llm.generate.return_value = json.dumps([{"text": "I use Rust for payroll."}])
    facts = FactExtractor(EncodingConfig(), llm=llm, mode=Mode.B)._extract_llm(
        [source], "wp5b", None, "", ""
    )
    live, _ = source_fact_set(facts, source, record, [], db=db)
    assert all(f.content != "I use Rust for payroll." for f in live)
    rows = db.execute(
        "SELECT f.content,f.quarantined,l.unresolved_reason FROM atomic_facts f "
        "JOIN derivation_lineage l ON l.object_id=f.fact_id"
    )
    assert len(rows) == 1 and rows[0]["quarantined"] == 1
    assert rows[0]["unresolved_reason"] == "source_fidelity:attribution_changed"


def test_delegated_entity_map_excludes_withheld_rows(tmp_path):
    from superlocalmemory.retrieval.entity_channel import EntityGraphChannel

    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=CASES[0][0])
    db.store_memory(record)
    live = AtomicFact(memory_id=record.memory_id, content=record.content)
    withheld = AtomicFact(memory_id=record.memory_id, content=CASES[0][1])
    db.store_fact(live)
    db.store_fact(withheld)
    db.execute("UPDATE atomic_facts SET quarantined=1 WHERE fact_id=?", (withheld.fact_id,))
    channel = EntityGraphChannel(db)
    channel._load_entity_maps("default")
    assert channel._visible_fact_ids == {live.fact_id}
