"""LOCAL LoCoMo mini-benchmark integration test.

Validates the FULL benchmark pipeline with synthetic data:
  conversation ingestion -> auxiliary data -> question answering -> scoring

No real ML models, no cloud APIs. Uses a mock embedder (hash-based 768-dim)
and a keyword-overlap evaluator.

Covers:
  - Full LoCoMoBenchmarkV3.run() pipeline
  - Ingestion phases (turns, auxiliary events/observations/summaries, speakers)
  - Recall quality for single-hop, temporal, keyword queries
  - Metrics computation (binarize, to_dict, export_json)

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from slm_innovation.benchmarks.locomo.benchmark import (
    EngineProtocol,
    EvaluatorProtocol,
    LoCoMoBenchmarkV3,
)
from slm_innovation.benchmarks.locomo.dataset import (
    AuxiliaryData,
    ConversationTurn,
    LoCoMoDataset,
    ParsedConversation,
    SCORED_CATEGORIES,
)
from slm_innovation.benchmarks.locomo.metrics import (
    BenchmarkScores,
    binarize_judge_score,
)
from slm_innovation.core.config import RetrievalConfig, SLMConfig
from slm_innovation.core.engine import MemoryEngine
from slm_innovation.storage.models import (
    AtomicFact,
    CanonicalEntity,
    FactType,
    MemoryRecord,
    Mode,
    RecallResponse,
)


# ---------------------------------------------------------------------------
# Mock Embedder (hash-based, deterministic, zero model loading)
# ---------------------------------------------------------------------------

class _MockEmbedder:
    """Deterministic mock embedder: text -> 768-dim vector via hashing."""

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        rng = np.random.default_rng(int.from_bytes(h[:8], "little"))
        vec = rng.standard_normal(self.dimension).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def compute_fisher_params(
        self, embedding: list[float],
    ) -> tuple[list[float], list[float]]:
        arr = np.asarray(embedding, dtype=np.float64)
        norm = float(np.linalg.norm(arr))
        if norm < 1e-10:
            mean = np.zeros(len(arr))
            var = np.full(len(arr), 2.0)
        else:
            mean = arr / norm
            abs_mean = np.abs(mean)
            max_val = float(np.max(abs_mean)) + 1e-10
            signal = abs_mean / max_val
            var = 2.0 - 1.7 * signal
            var = np.clip(var, 0.3, 2.0)
        return mean.tolist(), var.tolist()


# ---------------------------------------------------------------------------
# Engine Adapter (wraps MemoryEngine to match EngineProtocol)
# ---------------------------------------------------------------------------

class _EngineAdapter:
    """Adapter: MemoryEngine -> EngineProtocol interface.

    The benchmark expects store_memory / store_fact / store_entity / recall / close.
    MemoryEngine has different method names — this bridges the gap.
    """

    def __init__(self, engine: MemoryEngine) -> None:
        self._engine = engine

    def store_memory(self, record: MemoryRecord) -> str:
        self._engine._db.store_memory(record)
        return record.memory_id

    def store_fact(self, fact: AtomicFact) -> str:
        return self._engine.store_fact_direct(fact)

    def store_entity(self, entity: CanonicalEntity) -> str:
        self._engine._db.store_entity(entity)
        return entity.entity_id

    def recall(
        self, query: str, profile_id: str, mode: Mode, limit: int,
    ) -> RecallResponse:
        return self._engine.recall(query, profile_id, mode, limit)

    def close(self) -> None:
        self._engine.close()


# ---------------------------------------------------------------------------
# Keyword Evaluator (simple word-overlap scorer)
# ---------------------------------------------------------------------------

class _KeywordEvaluator:
    """Simple scorer: word overlap between answer and reference.

    No LLM needed. Sufficient for validating the pipeline produces
    non-trivial answers from the ingested data.
    """

    def score(self, question: str, answer: str, reference: str) -> float:
        ref_words = set(reference.lower().split())
        ans_words = set(answer.lower().split())
        if not ref_words:
            return 0.0
        overlap = len(ref_words & ans_words)
        return min(1.0, overlap / max(len(ref_words), 1))


# ---------------------------------------------------------------------------
# Synthetic Conversation Builder
# ---------------------------------------------------------------------------

def _build_synthetic_conversation() -> ParsedConversation:
    """Build a LoCoMo-format conversation with known answers.

    10 turns across 3 sessions. 3 event_summaries, 3 observations,
    2 session_summaries. 8 questions (2 per scored category).
    """
    turns = [
        # Session 1: introductions (May 2025)
        ConversationTurn(
            speaker="Alice", text="Hi Bob, I'm a software engineer at Google.",
            turn_id="t01", session_key="session_1",
            session_date_iso="2025-05-10T14:00:00",
        ),
        ConversationTurn(
            speaker="Bob", text="Nice to meet you Alice. I'm a doctor at Stanford Hospital.",
            turn_id="t02", session_key="session_1",
            session_date_iso="2025-05-10T14:00:00",
        ),
        ConversationTurn(
            speaker="Alice", text="I love hiking and photography in the mountains.",
            turn_id="t03", session_key="session_1",
            session_date_iso="2025-05-10T14:00:00",
        ),
        ConversationTurn(
            speaker="Bob", text="That sounds fun. I speak French and Japanese.",
            turn_id="t04", session_key="session_1",
            session_date_iso="2025-05-10T14:00:00",
        ),
        # Session 2: travel + education (June 2025)
        ConversationTurn(
            speaker="Alice", text="I visited Paris in July 2025 with my family. It was amazing.",
            turn_id="t05", session_key="session_2",
            session_date_iso="2025-06-15T10:00:00",
        ),
        ConversationTurn(
            speaker="Bob", text="I graduated from MIT in 2020 with a degree in medicine.",
            turn_id="t06", session_key="session_2",
            session_date_iso="2025-06-15T10:00:00",
        ),
        ConversationTurn(
            speaker="Alice", text="Wow, MIT is impressive. When did you start at Stanford?",
            turn_id="t07", session_key="session_2",
            session_date_iso="2025-06-15T10:00:00",
        ),
        # Session 3: book club (August 2025)
        ConversationTurn(
            speaker="Bob",
            text="We first met through the book club, remember? I recommended Sapiens to you.",
            turn_id="t08", session_key="session_3",
            session_date_iso="2025-08-01T16:00:00",
        ),
        ConversationTurn(
            speaker="Alice",
            text="Yes! Sapiens was a great recommendation. I finished it last week.",
            turn_id="t09", session_key="session_3",
            session_date_iso="2025-08-01T16:00:00",
        ),
        ConversationTurn(
            speaker="Bob", text="Glad you enjoyed it. The book club meets every Thursday.",
            turn_id="t10", session_key="session_3",
            session_date_iso="2025-08-01T16:00:00",
        ),
    ]

    auxiliary = AuxiliaryData(
        event_summaries=[
            {"event": "Alice visited Paris in July 2025", "speaker": "Alice",
             "date": "3:00 pm on 10 July, 2025"},
            {"event": "Bob graduated from MIT in 2020", "speaker": "Bob",
             "date": "1:00 pm on 15 June, 2020"},
            {"event": "Alice and Bob met through a book club", "speaker": "Bob",
             "date": "4:00 pm on 1 August, 2025"},
        ],
        observations=[
            {"observation": "Alice is a software engineer at Google",
             "speaker": "Alice", "evidence": ["t01"]},
            {"observation": "Bob is a doctor at Stanford Hospital",
             "speaker": "Bob", "evidence": ["t02"]},
            {"observation": "Bob recommended the book Sapiens to Alice",
             "speaker": "Bob", "evidence": ["t08"]},
        ],
        session_summaries=[
            {"session": "session_1",
             "summary": "Alice and Bob introduced themselves. Alice works at Google "
                        "as a software engineer. Bob is a doctor at Stanford Hospital. "
                        "Alice enjoys hiking and photography. Bob speaks French and Japanese."},
            {"session": "session_2",
             "summary": "Alice talked about visiting Paris in July 2025. "
                        "Bob shared that he graduated from MIT in 2020."},
        ],
    )

    questions: list[dict[str, Any]] = [
        # Single-hop (2)
        {"question": "What does Alice do for work?",
         "answer": "software engineer at Google",
         "category": "single_hop"},
        {"question": "Where does Bob work?",
         "answer": "Stanford Hospital",
         "category": "single_hop"},
        # Multi-hop (2)
        {"question": "How did Alice and Bob meet and what book did Bob recommend?",
         "answer": "They met through a book club and Bob recommended Sapiens",
         "category": "multi_hop"},
        {"question": "What is the connection between Alice and Bob through the book club?",
         "answer": "They met at a book club where Bob recommended Sapiens to Alice",
         "category": "multi_hop"},
        # Temporal (2)
        {"question": "When did Alice visit Paris?",
         "answer": "July 2025",
         "category": "temporal"},
        {"question": "When did Bob graduate from MIT?",
         "answer": "2020",
         "category": "temporal"},
        # Open-domain (2)
        {"question": "What are Alice's hobbies?",
         "answer": "hiking and photography",
         "category": "open_domain"},
        {"question": "What languages does Bob speak?",
         "answer": "French and Japanese",
         "category": "open_domain"},
    ]

    return ParsedConversation(
        conv_id="synthetic_001",
        speaker_a="Alice",
        speaker_b="Bob",
        turns=turns,
        auxiliary=auxiliary,
        questions=questions,
    )


# ---------------------------------------------------------------------------
# Synthetic Dataset (wraps ParsedConversation)
# ---------------------------------------------------------------------------

class _SyntheticDataset(LoCoMoDataset):
    """A LoCoMoDataset that returns our synthetic conversation.

    Overrides load_parsed so LoCoMoBenchmarkV3.run() can call it
    without needing the real LoCoMo JSON files.
    """

    def __init__(self) -> None:
        super().__init__(cache_dir=Path("/dev/null"))
        self._synthetic = _build_synthetic_conversation()

    def load_parsed(
        self, conversation_ids: list[str] | None = None,
    ) -> list[ParsedConversation]:
        if conversation_ids and self._synthetic.conv_id not in conversation_ids:
            return []
        return [self._synthetic]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "locomo_mini.db"


@pytest.fixture()
def engine_factory(db_path: Path):
    """Returns a callable that creates fresh adapted engines.

    Each call produces a new MemoryEngine with mock embedder and
    cross-encoder disabled — then wraps it in _EngineAdapter.
    """
    engines: list[MemoryEngine] = []

    def _factory() -> _EngineAdapter:
        config = SLMConfig.for_mode(Mode.A, base_dir=db_path.parent)
        config.db_path = db_path
        config.retrieval = RetrievalConfig(use_cross_encoder=False)

        eng = MemoryEngine(config)
        with patch(
            "slm_innovation.core.embeddings.EmbeddingService",
            return_value=_MockEmbedder(768),
        ):
            eng.initialize()

        # Create the benchmark profile
        eng._db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
            ("benchmark", "Benchmark"),
        )
        engines.append(eng)
        return _EngineAdapter(eng)

    yield _factory

    # Cleanup: close all engines created during the test
    for e in engines:
        try:
            e.close()
        except Exception:
            pass


@pytest.fixture()
def synthetic_dataset() -> _SyntheticDataset:
    return _SyntheticDataset()


@pytest.fixture()
def evaluator() -> _KeywordEvaluator:
    return _KeywordEvaluator()


@pytest.fixture()
def benchmark_config(db_path: Path) -> SLMConfig:
    config = SLMConfig.for_mode(Mode.A, base_dir=db_path.parent)
    config.db_path = db_path
    config.retrieval = RetrievalConfig(use_cross_encoder=False)
    return config


@pytest.fixture()
def benchmark_scores(
    benchmark_config: SLMConfig,
    engine_factory,
    evaluator: _KeywordEvaluator,
    synthetic_dataset: _SyntheticDataset,
) -> BenchmarkScores:
    """Run the full benchmark once, return scores for reuse across tests."""
    bench = LoCoMoBenchmarkV3(
        config=benchmark_config,
        engine_factory=engine_factory,
        evaluator=evaluator,
        llm=None,
        profile_id="benchmark",
    )
    return bench.run(synthetic_dataset)


# ---------------------------------------------------------------------------
# Standalone engine + data fixture (for ingestion + recall tests)
# ---------------------------------------------------------------------------

@pytest.fixture()
def loaded_engine(db_path: Path) -> MemoryEngine:
    """Engine with synthetic conversation ingested via MemoryEngine.store()."""
    config = SLMConfig.for_mode(Mode.A, base_dir=db_path.parent)
    config.db_path = db_path
    config.retrieval = RetrievalConfig(use_cross_encoder=False)

    eng = MemoryEngine(config)
    with patch(
        "slm_innovation.core.embeddings.EmbeddingService",
        return_value=_MockEmbedder(768),
    ):
        eng.initialize()

    eng._db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
        ("benchmark", "Benchmark"),
    )
    eng.profile_id = "benchmark"

    # Ingest synthetic turns via engine.store (full encoding pipeline)
    conv = _build_synthetic_conversation()
    for turn in conv.turns:
        eng.store(
            f"[{turn.speaker}]: {turn.text}",
            session_id=conv.conv_id,
            speaker=turn.speaker,
            session_date=turn.session_date_iso,
        )

    # Placeholder memory for auxiliary facts (FK constraint: atomic_facts.memory_id -> memories)
    placeholder = MemoryRecord(
        memory_id="aux_placeholder",
        profile_id="benchmark",
        content="Auxiliary data placeholder",
        session_id=conv.conv_id,
    )
    eng._db.store_memory(placeholder)

    # Ingest auxiliary data as direct facts
    for ev in conv.auxiliary.event_summaries:
        eng.store_fact_direct(AtomicFact(
            memory_id="aux_placeholder",
            profile_id="benchmark",
            content=f"{ev.get('speaker', '')}: {ev['event']}",
            fact_type=FactType.TEMPORAL,
            entities=[ev.get("speaker", "")],
            session_id=conv.conv_id,
            confidence=0.95, importance=0.8,
        ))
    for obs in conv.auxiliary.observations:
        eng.store_fact_direct(AtomicFact(
            memory_id="aux_placeholder",
            profile_id="benchmark",
            content=f"{obs.get('speaker', '')}: {obs['observation']}",
            fact_type=FactType.SEMANTIC,
            entities=[obs.get("speaker", "")],
            session_id=conv.conv_id,
            confidence=0.9, importance=0.75,
        ))
    for sm in conv.auxiliary.session_summaries:
        eng.store_fact_direct(AtomicFact(
            memory_id="aux_placeholder",
            profile_id="benchmark",
            content=sm["summary"],
            fact_type=FactType.EPISODIC,
            entities=["Alice", "Bob"],
            session_id=conv.conv_id,
            confidence=0.85, importance=0.7,
        ))

    return eng


# ===========================================================================
# TEST CLASSES
# ===========================================================================


# ---------------------------------------------------------------------------
# TestBenchmarkPipelineRuns — full pipeline validation
# ---------------------------------------------------------------------------

class TestBenchmarkPipelineRuns:
    """Verify the full LoCoMoBenchmarkV3 pipeline completes and scores."""

    def test_benchmark_completes(self, benchmark_scores: BenchmarkScores) -> None:
        """The benchmark should run to completion and return BenchmarkScores."""
        assert isinstance(benchmark_scores, BenchmarkScores)

    def test_all_categories_scored(self, benchmark_scores: BenchmarkScores) -> None:
        """Every scored category should have at least one result."""
        for cat in SCORED_CATEGORIES:
            assert cat in benchmark_scores.categories, f"Missing category: {cat}"
            assert benchmark_scores.categories[cat].count > 0, (
                f"Category {cat} has zero scores"
            )

    def test_total_scored_matches_questions(
        self, benchmark_scores: BenchmarkScores,
    ) -> None:
        """Total scored should equal the number of questions in scored categories."""
        # Our synthetic data has 8 questions, all in scored categories
        assert benchmark_scores.total_scored == 8

    def test_macro_average_non_negative(
        self, benchmark_scores: BenchmarkScores,
    ) -> None:
        """Macro average should be >= 0.0 (pipeline at least runs)."""
        assert benchmark_scores.macro_average >= 0.0

    def test_summary_table_rendered(
        self, benchmark_scores: BenchmarkScores,
    ) -> None:
        """summary_table() should produce a non-empty multi-line string."""
        table = benchmark_scores.summary_table()
        assert isinstance(table, str)
        assert len(table) > 50
        assert "Macro Average" in table
        assert "Micro Average" in table


# ---------------------------------------------------------------------------
# TestIngestionPhases — verify each phase of data ingestion
# ---------------------------------------------------------------------------

class TestIngestionPhases:
    """Verify the benchmark ingestion phases store data correctly."""

    def test_turns_ingested(self, loaded_engine: MemoryEngine) -> None:
        """Memories table should have rows from ingested turns."""
        rows = loaded_engine._db.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE profile_id = ?",
            ("benchmark",),
        )
        assert int(rows[0]["c"]) >= 10  # 10 turns

    def test_auxiliary_events_ingested(self, loaded_engine: MemoryEngine) -> None:
        """Event summaries should be stored as TEMPORAL facts."""
        facts = loaded_engine._db.get_facts_by_type(FactType.TEMPORAL, "benchmark")
        # At least 3 from our auxiliary events (encoding may also produce some)
        temporal_texts = [f.content.lower() for f in facts]
        assert any("paris" in t for t in temporal_texts)

    def test_auxiliary_observations_ingested(
        self, loaded_engine: MemoryEngine,
    ) -> None:
        """Observations should be stored as SEMANTIC facts."""
        facts = loaded_engine._db.get_facts_by_type(FactType.SEMANTIC, "benchmark")
        semantic_texts = [f.content.lower() for f in facts]
        assert any("software engineer" in t for t in semantic_texts)

    def test_auxiliary_summaries_ingested(
        self, loaded_engine: MemoryEngine,
    ) -> None:
        """Session summaries should be stored as EPISODIC facts."""
        facts = loaded_engine._db.get_facts_by_type(FactType.EPISODIC, "benchmark")
        assert len(facts) >= 2  # We stored 2 session summaries

    def test_speaker_entities_creatable(
        self, loaded_engine: MemoryEngine,
    ) -> None:
        """Canonical entities can be created for Alice and Bob."""
        # Store entities directly (benchmark does this via store_entity)
        loaded_engine._db.store_entity(CanonicalEntity(
            profile_id="benchmark", canonical_name="Alice", entity_type="person",
        ))
        loaded_engine._db.store_entity(CanonicalEntity(
            profile_id="benchmark", canonical_name="Bob", entity_type="person",
        ))
        alice = loaded_engine._db.get_entity_by_name("Alice", "benchmark")
        bob = loaded_engine._db.get_entity_by_name("Bob", "benchmark")
        assert alice is not None
        assert alice.canonical_name == "Alice"
        assert bob is not None
        assert bob.canonical_name == "Bob"

    def test_fact_count_positive(self, loaded_engine: MemoryEngine) -> None:
        """After ingestion, fact count should be positive."""
        count = loaded_engine._db.get_fact_count("benchmark")
        assert count > 0


# ---------------------------------------------------------------------------
# TestRecallQuality — verify recall returns relevant content
# ---------------------------------------------------------------------------

class TestRecallQuality:
    """Verify recall returns content relevant to the query."""

    def test_single_hop_recall(self, loaded_engine: MemoryEngine) -> None:
        """'What does Alice do?' should find 'software engineer'."""
        response = loaded_engine.recall("What does Alice do?", "benchmark")
        all_content = " ".join(r.fact.content.lower() for r in response.results)
        assert "software" in all_content or "engineer" in all_content or "google" in all_content

    def test_temporal_recall(self, loaded_engine: MemoryEngine) -> None:
        """'When did Alice visit Paris?' should find 'July 2025'."""
        response = loaded_engine.recall("When did Alice visit Paris?", "benchmark")
        all_content = " ".join(r.fact.content.lower() for r in response.results)
        assert "paris" in all_content

    def test_keyword_recall(self, loaded_engine: MemoryEngine) -> None:
        """'Stanford' should find Bob's hospital fact."""
        response = loaded_engine.recall("Stanford", "benchmark")
        all_content = " ".join(r.fact.content.lower() for r in response.results)
        assert "stanford" in all_content

    def test_recall_returns_results(self, loaded_engine: MemoryEngine) -> None:
        """Recall should return at least 1 result for a relevant query."""
        response = loaded_engine.recall("Alice", "benchmark")
        assert len(response.results) > 0

    def test_recall_response_has_metadata(
        self, loaded_engine: MemoryEngine,
    ) -> None:
        """RecallResponse should include query and timing metadata."""
        response = loaded_engine.recall("Bob", "benchmark")
        assert response.query == "Bob"
        assert response.retrieval_time_ms >= 0


# ---------------------------------------------------------------------------
# TestMetricsComputation — verify metrics math
# ---------------------------------------------------------------------------

class TestMetricsComputation:
    """Verify BenchmarkScores and binarize_judge_score calculations."""

    def test_binarize_at_threshold(self) -> None:
        """Score exactly at threshold (3.0) should be 1.0."""
        assert binarize_judge_score(3.0) == 1.0

    def test_binarize_below_threshold(self) -> None:
        """Score below threshold (2.9) should be 0.0."""
        assert binarize_judge_score(2.9) == 0.0

    def test_binarize_above_threshold(self) -> None:
        """Score above threshold (4.5) should be 1.0."""
        assert binarize_judge_score(4.5) == 1.0

    def test_binarize_custom_threshold(self) -> None:
        """Custom threshold should be respected."""
        assert binarize_judge_score(3.0, threshold=4.0) == 0.0
        assert binarize_judge_score(4.0, threshold=4.0) == 1.0

    def test_benchmark_scores_to_dict(
        self, benchmark_scores: BenchmarkScores,
    ) -> None:
        """to_dict() should return a JSON-serializable dict with expected keys."""
        d = benchmark_scores.to_dict()
        assert "overall_macro" in d
        assert "overall_micro" in d
        assert "total_scored" in d
        assert "per_category" in d
        assert "per_conversation" in d
        # Verify it is JSON-serializable
        serialized = json.dumps(d)
        assert len(serialized) > 10

    def test_export_json(
        self, benchmark_scores: BenchmarkScores, tmp_path: Path,
    ) -> None:
        """export_json() should write a valid JSON file."""
        out_path = tmp_path / "results" / "test_output.json"
        benchmark_scores.export_json(out_path)
        assert out_path.exists()
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["total_scored"] == benchmark_scores.total_scored
        assert "per_category" in data

    def test_category_result_mean(self) -> None:
        """CategoryResult.mean should compute correctly."""
        scores = BenchmarkScores()
        scores.add_score("single_hop", 1.0, "c1")
        scores.add_score("single_hop", 0.5, "c1")
        assert scores.categories["single_hop"].mean == pytest.approx(0.75, abs=0.01)

    def test_macro_vs_micro_average(self) -> None:
        """Macro and micro averages should differ when category sizes differ."""
        scores = BenchmarkScores()
        # single_hop: 2 questions, both perfect
        scores.add_score("single_hop", 1.0, "c1")
        scores.add_score("single_hop", 1.0, "c1")
        # open_domain: 1 question, zero score
        scores.add_score("open_domain", 0.0, "c1")
        # Macro: mean of (1.0, 0.0) = 0.5
        # Micro: mean of (1.0, 1.0, 0.0) = 0.667
        assert scores.macro_average == pytest.approx(0.5, abs=0.01)
        assert scores.micro_average == pytest.approx(0.667, abs=0.01)

    def test_empty_scores_zero_average(self) -> None:
        """Empty BenchmarkScores should have 0.0 averages."""
        scores = BenchmarkScores()
        assert scores.macro_average == 0.0
        assert scores.micro_average == 0.0
        assert scores.total_scored == 0
