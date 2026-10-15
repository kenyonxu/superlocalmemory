# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The evidence the answer-support gate reads: rare terms, script, query profile."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from superlocalmemory.retrieval import evidence_features as ef
from superlocalmemory.retrieval import hub_baseline as hb


def test_latin_script_is_judged_on_letters_only() -> None:
    assert ef.is_latin("How long does ensure_daemon wait?")
    assert ef.is_latin("Wann läuft die nächtliche Sicherung?")
    assert ef.is_latin("asdk_app_6ac9e8 v0.3.4")
    assert ef.is_latin("12345 ???")
    assert not ef.is_latin("日志保留多久？")
    assert not ef.is_latin("डेटाबेस का बैकअप कब लिया जाता है?")
    assert not ef.is_latin("ステージング環境のポートは何番ですか？")


def test_rare_terms_are_identifiers_and_words_no_dictionary_knows() -> None:
    terms = ["v0.3.4", "snazzle", "release", "kestrel", "läuft", "right-to-be-forgotten",
             "p1", "ci", "bananas"]
    assert ef.rare_terms(terms, vocab=frozenset()) == ["v0.3.4", "snazzle", "kestrel", "p1"]
    # A word the model's vocabulary knows is not rare either.
    assert ef.rare_terms(["kestrel"], vocab=frozenset({"kestrel"})) == []
    assert ef.rare_terms([], vocab=None) == []


def test_rare_miss_is_the_share_of_rare_terms_the_memory_lacks() -> None:
    content = "Hub v0.3.4 fixed the resultType bug."
    assert ef.rare_miss(["v0.3.4", "snazzle"], content) == pytest.approx(0.5)
    assert ef.rare_miss(["v0.3.4"], content) == 0.0
    assert ef.rare_miss(["asdk_app_ffff"], "rcpt asdk_app_6ac9 ok") == 1.0
    assert ef.rare_miss([], content) == 0.0


def test_build_records_rare_miss_only_when_rare_terms_are_given() -> None:
    fact = SimpleNamespace(content="Hub v0.3.4 fixed it", embedding=None,
                           canonical_entities=[], entities=[])
    out = ef.build(query="v0.3.4 snazzle", terms=["v0.3.4", "snazzle"], query_vector=None,
                   fact=fact, channel_scores={}, rare=["v0.3.4", "snazzle"])
    assert out["rare_miss"] == pytest.approx(0.5)
    plain = ef.build(query="v0.3.4", terms=["v0.3.4"], query_vector=None, fact=fact,
                     channel_scores={})
    assert plain["rare_miss"] == 0.0


def test_query_evidence_reports_script_rare_terms_and_query_profile(monkeypatch) -> None:
    from superlocalmemory.retrieval import query_lexicon

    monkeypatch.setattr(query_lexicon, "vocabulary", lambda: frozenset())
    q = ef.query_evidence("v0.3.4 snazzle", nonsense_similarity=0.41234567)
    assert q["latin_script"] is True
    assert q["rare_terms"] == 2
    assert q["nonsense_similarity"] == pytest.approx(0.412346)
    assert ef.query_evidence("日志保留多久？")["latin_script"] is False
    assert ef.query_evidence("what is this")["nonsense_similarity"] is None


def _probe_matrix(dim: int = 4) -> np.ndarray:
    rows = np.zeros((96, dim), dtype=np.float32)
    rows[:, 0] = 1.0
    rows[32:64, 0] = 0.0
    rows[32:64, 1] = 1.0  # the "ordinary words, no meaning" probes
    return rows


def test_query_profile_is_the_mean_cosine_to_the_meaningless_sentences() -> None:
    probes = _probe_matrix()
    assert hb.query_profile([0.0, 1.0, 0.0, 0.0], probes) == pytest.approx(1.0)
    assert hb.query_profile([1.0, 0.0, 0.0, 0.0], probes) == pytest.approx(0.0)
    assert hb.query_profile(None, probes) is None
    assert hb.query_profile([1.0, 0.0], probes) is None  # another space
    assert hb.query_profile([1.0, 0.0, 0.0, 0.0], None) is None


def test_baseline_of_one_vector_matches_the_stored_form() -> None:
    probes = _probe_matrix()
    topk, mean = hb.baseline_of([1.0, 0.0, 0.0, 0.0], probes)
    (stored_mean, stored_top), = hb.baselines([[1.0, 0.0, 0.0, 0.0]], probes)
    assert (topk, mean) == pytest.approx((stored_top, stored_mean))
    assert hb.baseline_of(None, probes) is None
    assert hb.baseline_of([1.0, 0.0], probes) is None


class _Embedder:
    def __init__(self):
        from superlocalmemory.core.config import EmbeddingConfig

        self._config = EmbeddingConfig(model_name="test/model", dimension=4)
        self.calls = 0

    def embed_batch(self, texts):
        self.calls += 1
        return [[1.0, float(i % 3), 0.0, 0.5] for i, _ in enumerate(texts)]


def test_cached_never_embeds_and_matrix_fills_the_cache() -> None:
    bank = hb.ProbeBank()
    emb = _Embedder()
    space = "space-under-test"
    assert bank.cached(space) is None
    assert emb.calls == 0
    m = bank.matrix(emb, space)
    assert m is not None and m.shape == (96, 4)
    assert bank.cached(space) is m
    assert emb.calls == 1


def _engine_with(probes, stored):
    from superlocalmemory.retrieval.engine import RetrievalEngine

    eng = RetrievalEngine.__new__(RetrievalEngine)
    eng._reranker = None
    eng._query_embedding_cache = {"v0.3.4 snazzle": [0.0, 1.0, 0.0, 0.0]}
    eng._hub_baselines = lambda ids, profile: stored
    eng._probe_matrix = lambda: probes
    return eng


def _result(fid, content, embedding):
    from superlocalmemory.storage.models import RetrievalResult

    fact = SimpleNamespace(fact_id=fid, content=content, embedding=embedding,
                           canonical_entities=[], entities=[])
    return RetrievalResult(fact=fact, score=0.7, channel_scores={"semantic": 0.9})


def test_attach_measures_a_missing_baseline_and_the_query_profile() -> None:
    probes = _probe_matrix()
    eng = _engine_with(probes, {"f1": (0.5, 0.25)})
    results = [_result("f1", "hub v0.3.4 fix", [0.0, 1.0, 0.0, 0.0]),
               _result("f2", "release notes", [1.0, 0.0, 0.0, 0.0])]
    q = eng._attach_evidence("v0.3.4 snazzle", results, {}, "default")
    assert q["nonsense_similarity"] == pytest.approx(1.0)
    assert q["rare_terms"] == 2
    assert results[0].evidence["hub_baseline"] == pytest.approx(0.5)  # stored wins
    assert results[0].evidence["rare_miss"] == pytest.approx(0.5)
    topk, _mean = hb.baseline_of([1.0, 0.0, 0.0, 0.0], probes)
    assert results[1].evidence["hub_baseline"] == pytest.approx(topk, abs=1e-6)
    assert results[1].evidence["rare_miss"] == 1.0


def test_attach_without_probes_leaves_the_profile_and_baseline_missing() -> None:
    eng = _engine_with(None, {})
    results = [_result("f1", "hub v0.3.4 fix", [0.0, 1.0, 0.0, 0.0])]
    q = eng._attach_evidence("v0.3.4 snazzle", results, {}, "default")
    assert q["nonsense_similarity"] is None
    assert results[0].evidence["hub_baseline"] is None


def test_warm_probes_embeds_once_and_fails_open(monkeypatch) -> None:
    bank = hb.ProbeBank()
    monkeypatch.setattr(hb, "PROBES", bank)
    monkeypatch.setattr(hb, "space_of", lambda emb: "space-under-test" if emb else None)
    emb = _Embedder()
    assert hb.warm_probes(emb) is True
    assert hb.warm_probes(emb) is True
    assert emb.calls == 1
    assert hb.warm_probes(None) is False

    class _Broken(_Embedder):
        def embed_batch(self, texts):
            raise RuntimeError("model gone")

    monkeypatch.setattr(hb, "PROBES", hb.ProbeBank())
    assert hb.warm_probes(_Broken()) is False


def test_daemon_warmup_embeds_the_probes(monkeypatch) -> None:
    from superlocalmemory.server import recall_warmup

    seen = []
    monkeypatch.setattr(hb, "warm_probes", lambda emb: seen.append(emb) or True)
    engine = SimpleNamespace(_embedder="E", profile_id="default")
    assert recall_warmup.warm_hub_probes(engine) is True
    assert seen == ["E"]


def test_cosine_falls_back_to_the_semantic_channel_when_no_vector_is_loaded() -> None:
    fact = SimpleNamespace(content="x", embedding=None, canonical_entities=[], entities=[])
    out = ef.build(query="q", terms=["q"], query_vector=[1.0, 0.0], fact=fact,
                   channel_scores={"semantic": 0.9})
    assert out["cosine"] == pytest.approx(0.8)


def test_the_floor_admits_a_memory_that_stands_out_from_its_own_hub_baseline() -> None:
    from superlocalmemory.retrieval.time_window import HUB_CORRECTED_DOOR, has_primary_evidence

    below = {"semantic": (0.59 + 1) / 2}  # raw cosine 0.59, under the 0.60 floor
    assert not has_primary_evidence(below, 0.60)
    assert has_primary_evidence(below, 0.60, hub_corrected=HUB_CORRECTED_DOOR)
    assert not has_primary_evidence(below, 0.60, hub_corrected=HUB_CORRECTED_DOOR - 0.01)


def test_hub_corrected_is_measured_only_for_memories_the_floor_would_drop() -> None:
    from superlocalmemory.retrieval.evidence_attach import floor_hub_corrected
    from superlocalmemory.retrieval.fusion import FusionResult

    probes = _probe_matrix()
    top = [FusionResult("keep", 0.5, channel_scores={"semantic": 0.9}),
           FusionResult("near", 0.4, channel_scores={"semantic": (0.59 + 1) / 2}),
           FusionResult("bm25", 0.3, channel_scores={"bm25": 0.4}),
           FusionResult("assoc", 0.2, channel_scores={"hopfield": 0.9})]
    facts = {fid: SimpleNamespace(fact_id=fid, embedding=[1.0, 0.0, 0.0, 0.0])
             for fid in ("keep", "near", "bm25", "assoc")}
    asked: list = []
    out = floor_hub_corrected(top, facts, 0.60, stored=lambda ids: asked.extend(ids) or {},
                              probes=probes)
    assert asked == ["near"]
    topk, _ = hb.baseline_of([1.0, 0.0, 0.0, 0.0], probes)
    assert out == {"near": pytest.approx(0.59 - topk, abs=1e-6)}
    assert floor_hub_corrected(top, facts, 0.60, stored=lambda ids: {"near": (0.4, 0.3)},
                               probes=None) == {"near": pytest.approx(0.19)}


def test_the_engine_floor_uses_the_hub_door() -> None:
    from superlocalmemory.retrieval.engine import RetrievalEngine
    from superlocalmemory.retrieval.fusion import FusionResult

    near = FusionResult("near", 0.4, channel_scores={"semantic": (0.59 + 1) / 2})
    facts = {"near": SimpleNamespace(fact_id="near", content="a note", pinned=False)}
    assert RetrievalEngine._apply_evidence_floor([near], facts, 0.60) == []
    kept = RetrievalEngine._apply_evidence_floor([near], facts, 0.60,
                                                 hub_corrected={"near": 0.15})
    assert kept == [near]


def test_orphan_terms_are_question_words_no_stored_memory_contains() -> None:
    terms = ef.query_terms("Which release notes mention the moon?")
    countable = ef.countable_terms(terms)
    assert countable == ["release", "notes", "mention", "moon"]
    assert ef.orphan_share(countable, {"release", "notes", "mention"}) == pytest.approx(0.25)
    assert ef.orphan_share([], set()) is None
    # Question-frame words are not judged; text in a script written without
    # spaces is judged by its character pairs (see the unspaced tests below).
    assert ef.countable_terms(ef.query_terms("How long did it take?")) == []
    assert ef.countable_terms(["läuft", "日志保留", "डेटाबेस", "бвг"]) == [
        "läuft", "日志", "志保", "保留", "डेटाबेस", "бвг"]


def test_attach_reports_the_orphan_share_from_the_store() -> None:
    eng = _engine_with(None, {})
    eng._terms_in_store = lambda terms, profile: {t for t in terms if t != "moon"}
    q = eng._attach_evidence("release notes for the moon", [], {}, "default")
    assert q["orphan_terms"] == pytest.approx(1 / 3)
    eng._terms_in_store = lambda terms, profile: (_ for _ in ()).throw(RuntimeError("no fts"))
    assert eng._attach_evidence("release notes for the moon", [], {}, "default")[
        "orphan_terms"] is None


def test_terms_in_store_reads_the_keyword_index_with_base_forms(tmp_path) -> None:
    import sqlite3

    from superlocalmemory.retrieval.engine import RetrievalEngine

    conn = sqlite3.connect(tmp_path / "m.db")
    conn.executescript("""
        CREATE TABLE atomic_facts (fact_id TEXT, profile_id TEXT, content TEXT);
        CREATE VIRTUAL TABLE atomic_facts_fts USING fts5(fact_id UNINDEXED, content,
            content='atomic_facts', content_rowid='rowid');
        INSERT INTO atomic_facts VALUES ('f1', 'p', 'The nightly job failed on v0.3.4');
        INSERT INTO atomic_facts VALUES ('f2', 'other', 'saxophone lessons');
        INSERT INTO atomic_facts_fts (rowid, fact_id, content)
            SELECT rowid, fact_id, content FROM atomic_facts;
    """)
    eng = RetrievalEngine.__new__(RetrievalEngine)
    eng._db = SimpleNamespace(execute=lambda sql, params=(): conn.execute(sql, params).fetchall())
    found = eng._terms_in_store(["nightly", "failing", "v0.3.4", "saxophone", "moon"], "p")
    assert found == {"nightly", "failing", "v0.3.4"}


def test_a_question_frame_is_a_question_word_or_auxiliary() -> None:
    assert ef.question_frame("How long did it take?")
    assert ef.question_frame("which port does the dashboard use")
    assert not ef.question_frame("ensure_daemon trance")
    assert not ef.question_frame("release notes for the moon")


def test_subject_coverage_counts_only_the_words_that_name_a_subject() -> None:
    fact = SimpleNamespace(content="ensure_daemon waits 12 seconds for the socket",
                           embedding=None, canonical_entities=[], entities=[])
    q = "ensure_daemon trance"
    out = ef.build(query=q, terms=ef.query_terms(q), query_vector=None, fact=fact,
                   channel_scores={})
    assert out["subject_coverage"] == pytest.approx(0.5)
    long_q = "How long does ensure_daemon wait?"  # "long" frames, "wait" != "waits"
    out = ef.build(query=long_q, terms=ef.query_terms(long_q), query_vector=None, fact=fact,
                   channel_scores={})
    assert out["subject_coverage"] == pytest.approx(0.5)
    jp = "日志保留多久？"  # judged on character pairs: none are in this memory
    assert ef.build(query=jp, terms=ef.query_terms(jp), query_vector=None, fact=fact,
                    channel_scores={})["subject_coverage"] == 0.0
    assert ef.build(query="??", terms=[], query_vector=None, fact=fact,
                    channel_scores={})["subject_coverage"] == 1.0


def test_a_recall_with_a_question_vector_embeds_the_probes_once(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(hb, "warm_probes", lambda emb: calls.append(emb) or True)
    monkeypatch.setattr(hb, "_LAST_LAZY_WARM", [0.0])
    eng = _engine_with(None, {})
    eng._embedder = "E"
    eng._config = SimpleNamespace(answer_support_gate=True)
    eng._attach_evidence("v0.3.4 snazzle", [], {}, "default")
    eng._attach_evidence("v0.3.4 snazzle", [], {}, "default")  # not again within a minute
    assert calls == ["E"]
    eng._query_embedding_cache = {}
    monkeypatch.setattr(hb, "_LAST_LAZY_WARM", [0.0])
    eng._attach_evidence("no vector", [], {}, "default")
    assert calls == ["E"]  # no question vector: the embedder is not ready


def test_at_most_twelve_words_are_looked_up_as_orphans() -> None:
    words = [f"word{i}x" for i in range(30)]
    assert len(ef.countable_terms(words)) == ef.MAX_COUNTABLE == 12


def test_unspaced_scripts_are_judged_on_character_pairs() -> None:
    terms = ef.query_terms("日志保留多久？")
    assert ef.countable_terms(terms) == ["日志", "志保", "保留", "留多", "多久"]
    fact = SimpleNamespace(content="我们的日志保留三十天。", embedding=None,
                           canonical_entities=[], entities=[])
    out = ef.build(query="日志保留多久？", terms=terms, query_vector=None, fact=fact,
                   channel_scores={})
    assert out["subject_coverage"] == pytest.approx(3 / 5)


def test_terms_in_store_finds_character_pairs_by_substring(tmp_path) -> None:
    import sqlite3

    from superlocalmemory.retrieval.engine import RetrievalEngine

    conn = sqlite3.connect(tmp_path / "m.db")
    conn.executescript("""
        CREATE TABLE atomic_facts (fact_id TEXT, profile_id TEXT, content TEXT);
        CREATE VIRTUAL TABLE atomic_facts_fts USING fts5(fact_id UNINDEXED, content,
            content='atomic_facts', content_rowid='rowid');
        INSERT INTO atomic_facts VALUES ('f1', 'p', '我们的日志保留三十天。');
        INSERT INTO atomic_facts_fts (rowid, fact_id, content)
            SELECT rowid, fact_id, content FROM atomic_facts;
    """)
    eng = RetrievalEngine.__new__(RetrievalEngine)
    eng._db = SimpleNamespace(execute=lambda sql, params=(): conn.execute(sql, params).fetchall())
    assert eng._terms_in_store(["日志", "多久", "ケブ"], "p") == {"日志"}
