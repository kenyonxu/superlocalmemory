# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Query rewriting widens what keyword search can find, and nothing else.

Deterministic in every mode (simple morphology, entity aliases and canonical
names); an optional local-model rewrite in Mode B, bounded and failing open to
the deterministic one. The rewrite only reaches the keyword channel: the
answer-support gate and the cross-encoder score the ORIGINAL question, so a
rewrite can surface a candidate but can never make nonsense answer.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from superlocalmemory.retrieval import query_rewrite as qr
from superlocalmemory.storage.models import Mode


def test_morphology_adds_base_forms_of_inflected_words() -> None:
    added = qr.morphology_terms("Why did the reranker handles keep failing?")
    assert "handle" in added and "fail" in added
    assert "reranker" not in added  # nothing to reduce
    assert qr.morphology_terms("asdk_app_6ac9 v0.3.4") == []


def _alias_db() -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE canonical_entities (entity_id TEXT, profile_id TEXT, canonical_name TEXT);
        CREATE TABLE entity_aliases (alias_id TEXT, profile_id TEXT, entity_id TEXT, alias TEXT);
        INSERT INTO canonical_entities VALUES ('e1', 'default', 'Tomás Rivera');
        INSERT INTO entity_aliases VALUES ('a1', 'default', 'e1', 'Tom');
        INSERT INTO entity_aliases VALUES ('a2', 'default', 'e1', 'TR');
        INSERT INTO canonical_entities VALUES ('e2', 'other', 'Secret Person');
        INSERT INTO entity_aliases VALUES ('a3', 'other', 'e2', 'Tom');
    """)
    return SimpleNamespace(execute=lambda sql, params=(): conn.execute(sql, params).fetchall())


def test_aliases_and_canonical_names_expand_each_other_within_the_profile() -> None:
    db = _alias_db()
    assert set(qr.alias_terms(db, "default", "What does Tom maintain?")) == {
        "Tomás Rivera", "TR"}
    assert "Tom" in qr.alias_terms(db, "default", "what did tomás rivera ship")
    assert qr.alias_terms(db, "default", "nothing named here") == []
    assert qr.alias_terms(SimpleNamespace(), "default", "Tom") == []  # no store: nothing


def test_rewrite_is_deterministic_in_mode_a_and_keeps_the_question() -> None:
    db = _alias_db()
    r = qr.rewrite("What does Tom keep failing at?", mode=Mode.A, db=db,
                   profile_id="default", llm=None, config=None)
    assert r.source == "deterministic"
    assert r.lexical_query.startswith("What does Tom keep failing at?")
    assert {"Tomás Rivera", "fail"} <= set(r.added)
    again = qr.rewrite("What does Tom keep failing at?", mode=Mode.A, db=db,
                       profile_id="default", llm=None, config=None)
    assert again == r


class _LLM:
    provider = "ollama"

    def __init__(self, reply="ssd shipment date", delay=0.0, fail=False):
        self.reply, self.delay, self.fail, self.calls = reply, delay, fail, 0

    def is_available(self):
        return True

    def generate(self, prompt, system="", temperature=None, max_tokens=None, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("ollama down")
        time.sleep(self.delay)
        return self.reply


def _cfg(llm_on=True, timeout=0.5):
    return SimpleNamespace(retrieval=SimpleNamespace(
        query_rewrite=True, query_rewrite_llm=llm_on, query_rewrite_timeout_s=timeout))


def test_mode_b_asks_the_local_model_when_switched_on() -> None:
    llm = _LLM()
    r = qr.rewrite("When did the drives arrive?", mode=Mode.B, db=None, profile_id="p",
                   llm=llm, config=_cfg())
    assert r.source == "local_model" and {"ssd", "shipment"} <= set(r.added)
    off = qr.rewrite("When did the drives arrive?", mode=Mode.B, db=None, profile_id="p",
                     llm=_LLM(), config=_cfg(llm_on=False))
    assert off.source == "deterministic"
    a = qr.rewrite("When did the drives arrive?", mode=Mode.A, db=None, profile_id="p",
                   llm=llm, config=_cfg())
    assert a.source == "deterministic" and llm.calls == 1  # Mode A never asks


@pytest.mark.parametrize("llm", [_LLM(delay=2.0), _LLM(fail=True)])
def test_a_slow_or_failing_model_falls_back_to_the_deterministic_rewrite(llm) -> None:
    t0 = time.monotonic()
    r = qr.rewrite("When did the drives arrive?", mode=Mode.B, db=None, profile_id="p",
                   llm=llm, config=_cfg(timeout=0.2))
    assert time.monotonic() - t0 < 1.0
    assert r.source == "deterministic"
    # The abandoned ask finishes on its own (the model's own timeout bounds it).
    for t in threading.enumerate():
        if t.name == "slm-query-rewrite":
            t.join(5.0)


def test_a_hosted_model_is_never_sent_the_question() -> None:
    llm = _LLM()
    llm.provider = "openai"
    r = qr.rewrite("When did the drives arrive?", mode=Mode.B, db=None, profile_id="p",
                   llm=llm, config=_cfg())
    assert r.source == "deterministic" and llm.calls == 0


def test_rewriting_never_lets_nonsense_through() -> None:
    """A rewrite that maps gibberish onto a stored identifier finds the memory,
    but the gate reads the original question's evidence and abstains."""
    from superlocalmemory.retrieval import evidence_features as ef
    from superlocalmemory.retrieval import support_gate as sg

    original = "zxqv plorth wimble"
    rewritten = "v0.3.4 hub fix"
    fact = SimpleNamespace(content="Hub v0.3.4 fixed the resultType bug.", embedding=None,
                           canonical_entities=[], entities=[])
    terms = ef.query_terms(original)
    evidence = ef.build(query=original, terms=terms, query_vector=None, fact=fact,
                        channel_scores={"bm25": 0.7, "semantic": 0.78},
                        ce_raw=-11.0, rare=ef.rare_terms(terms, frozenset()))
    qe = ef.query_evidence(original, terms, nonsense_similarity=0.44)
    assert not sg.assess([SimpleNamespace(fact=fact, evidence=evidence)], qe).supported
    # The same memory against the rewrite would read as support -- which is
    # exactly why the gate never sees the rewrite.
    terms2 = ef.query_terms(rewritten)
    ev2 = ef.build(query=rewritten, terms=terms2, query_vector=None, fact=fact,
                   channel_scores={"bm25": 0.7, "semantic": 0.78}, ce_raw=6.0,
                   rare=ef.rare_terms(terms2, frozenset()))
    qe2 = ef.query_evidence(rewritten, terms2, nonsense_similarity=0.33)
    assert sg.assess([SimpleNamespace(fact=fact, evidence=ev2)], qe2).supported


def test_run_recall_sends_the_rewrite_to_keyword_search_only(monkeypatch) -> None:
    from superlocalmemory.core import recall_pipeline
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import RecallResponse

    seen: list = []

    def recall(query, *a, **kw):
        seen.append((query, kw.get("lexical_query")))
        return RecallResponse(query=query)

    engine = SimpleNamespace(_sufficiency_judge=None, recall=recall)
    monkeypatch.setattr(recall_pipeline, "apply_ranking", lambda resp, *a, **k: resp)
    config = SLMConfig.for_mode(Mode.A)
    recall_pipeline.run_recall(
        "Why do handles keep failing?", "default", fast=True, config=config,
        retrieval_engine=engine, trust_scorer=None, embedder=None,
        db=SimpleNamespace(db_path=None), llm=None, hooks=None)
    question, lexical = seen[0]
    assert question == "Why do handles keep failing?"
    assert lexical.startswith(question) and "handle" in lexical
    recall_pipeline.run_recall(
        "ensure_daemon", "default", fast=True, config=config,
        retrieval_engine=engine, trust_scorer=None, embedder=None,
        db=SimpleNamespace(db_path=None), llm=None, hooks=None)
    assert seen[1] == ("ensure_daemon", None)  # nothing to add: nothing passed


def test_the_keyword_channel_reads_the_lexical_query_and_nothing_else_does() -> None:
    import concurrent.futures

    from superlocalmemory.retrieval.engine import RetrievalEngine

    asked: dict[str, str] = {}

    class _Channel:
        def __init__(self, name):
            self.name = name

        def search(self, query, profile_id, top_k, **kw):
            asked[self.name] = query
            return []

    eng = RetrievalEngine.__new__(RetrievalEngine)
    eng._config = SimpleNamespace(disabled_channels=["media"], bm25_top_k=5)
    eng._semantic = eng._hopfield = eng._spreading_activation = None
    eng._bm25, eng._temporal = _Channel("bm25"), _Channel("temporal")
    eng._channel_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        eng._run_channels("handles failing", "default", SimpleNamespace(query_type="factual"),
                          lexical_query="handles failing handle fail")
        assert asked == {"bm25": "handles failing handle fail", "temporal": "handles failing"}
        eng._run_channels("handles failing", "default", SimpleNamespace(query_type="factual"))
        assert asked["bm25"] == "handles failing"
    finally:
        eng._channel_executor.shutdown(wait=True)
    assert threading.active_count() >= 1
