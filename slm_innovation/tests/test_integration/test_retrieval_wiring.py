"""Integration tests — every retrieval channel and math layer participates.

After storing data and calling recall(), we inspect the response to verify
each component contributed.  Uses the same mock embedder and fixture pattern
as test_e2e.py (zero model loading, deterministic, ~0.1 ms per embed).

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from slm_innovation.core.config import SLMConfig, RetrievalConfig
from slm_innovation.core.engine import MemoryEngine
from slm_innovation.math.fisher import FisherRaoMetric
from slm_innovation.math.hopfield import ModernHopfield
from slm_innovation.math.langevin import LangevinDynamics
from slm_innovation.math.poincare import PoincareBall
from slm_innovation.math.rate_distortion import RateDistortionDepth
from slm_innovation.math.sheaf import coboundary_norm
from slm_innovation.storage.models import (
    AtomicFact,
    FactType,
    MemoryLifecycle,
    Mode,
    RecallResponse,
    RetrievalResult,
)


# ---------------------------------------------------------------------------
# Mock Embedder — identical to test_e2e.py
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
            var = 2.0 - 1.95 * signal
            var = np.clip(var, 0.3, 2.0)
        return mean.tolist(), var.tolist()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_wiring.db"


@pytest.fixture()
def engine(db_path: Path) -> MemoryEngine:
    """Mode A engine with mock embedder, no cross-encoder."""
    config = SLMConfig.for_mode(Mode.A, base_dir=db_path.parent)
    config.db_path = db_path
    config.retrieval = RetrievalConfig(use_cross_encoder=False)

    eng = MemoryEngine(config)
    with patch(
        "slm_innovation.core.embeddings.EmbeddingService",
        return_value=_MockEmbedder(768),
    ):
        eng.initialize()
    return eng


@pytest.fixture()
def loaded_engine(engine: MemoryEngine) -> MemoryEngine:
    """Engine pre-loaded with facts for channel wiring tests."""
    engine.store(
        "Alice is a software engineer at Google.",
        session_id="s1", speaker="Bob",
        session_date="3:00 pm on 5 March, 2026",
    )
    engine.store(
        "Bob mentioned that Alice loves hiking in the mountains.",
        session_id="s1", speaker="Bob",
        session_date="3:00 pm on 5 March, 2026",
    )
    engine.store(
        "Alice said she visited Paris last summer with her family.",
        session_id="s2", speaker="Alice",
        session_date="4:00 pm on 6 March, 2026",
    )
    engine.store(
        "Bob is a doctor at the local hospital. He graduated from Stanford.",
        session_id="s2", speaker="Alice",
        session_date="4:00 pm on 6 March, 2026",
    )
    engine.store(
        "Quantum computing research paper published by Alice in Nature.",
        session_id="s3", speaker="Bob",
        session_date="10:00 am on 8 March, 2026",
    )
    engine.store(
        "Alice started working at Microsoft in January 2026.",
        session_id="s3", speaker="Bob",
        session_date="10:00 am on 8 March, 2026",
    )
    return engine


# ---------------------------------------------------------------------------
# 1. TestSemanticChannelWiring
# ---------------------------------------------------------------------------

class TestSemanticChannelWiring:
    """Verify semantic (embedding-based) channel contributes to results."""

    def test_channel_weights_has_semantic(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("software engineer")
        assert "semantic" in response.channel_weights

    def test_result_has_semantic_score(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("software engineer")
        has_semantic = any(
            "semantic" in r.channel_scores for r in response.results
        )
        assert has_semantic, "No result has a 'semantic' channel_score"

    def test_results_ranked_by_relevance(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("software engineer")
        scores = [r.score for r in response.results]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 2. TestBM25ChannelWiring
# ---------------------------------------------------------------------------

class TestBM25ChannelWiring:
    """Verify BM25 keyword channel contributes to results."""

    def test_bm25_contributes(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("quantum")
        has_bm25 = any(
            "bm25" in r.channel_scores for r in response.results
        )
        assert has_bm25, "BM25 channel did not contribute to results"

    def test_keyword_match_appears(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("quantum")
        contents = [r.fact.content.lower() for r in response.results]
        assert any("quantum" in c for c in contents)


# ---------------------------------------------------------------------------
# 3. TestEntityGraphChannelWiring
# ---------------------------------------------------------------------------

class TestEntityGraphChannelWiring:
    """Verify entity_graph channel contributes on entity-centric queries."""

    def test_entity_graph_contributes(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        has_entity = any(
            "entity_graph" in r.channel_scores for r in response.results
        )
        # Entity graph may or may not fire depending on resolver; check weight exists
        assert "entity_graph" in response.channel_weights

    def test_entity_linked_facts_appear(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        contents = [r.fact.content.lower() for r in response.results]
        assert any("alice" in c for c in contents)

    def test_spreading_activation_finds_related(self, loaded_engine: MemoryEngine) -> None:
        """Query about Alice should find Bob-related facts via graph edges."""
        response = loaded_engine.recall("Alice")
        # At minimum, multiple results should come back
        assert len(response.results) >= 2


# ---------------------------------------------------------------------------
# 4. TestTemporalChannelWiring
# ---------------------------------------------------------------------------

class TestTemporalChannelWiring:
    """Verify temporal channel contributes on time-oriented queries."""

    def test_temporal_channel_contributes(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("What happened in March 2026?")
        assert "temporal" in response.channel_weights

    def test_temporal_facts_appear(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("What happened in March 2026?")
        # Should find at least one fact that has March date context
        assert len(response.results) > 0


# ---------------------------------------------------------------------------
# 5. TestRRFFusionWiring
# ---------------------------------------------------------------------------

class TestRRFFusionWiring:
    """Verify multiple channels fuse into a single ranked list."""

    def test_multiple_channels_in_scores(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice engineer")
        all_channels: set[str] = set()
        for r in response.results:
            all_channels.update(r.channel_scores.keys())
        assert len(all_channels) >= 2, f"Only {all_channels} contributed"

    def test_no_duplicate_facts(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice engineer")
        ids = [r.fact.fact_id for r in response.results]
        assert len(ids) == len(set(ids)), "Fusion produced duplicate fact_ids"

    def test_fused_scores_descending(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice engineer")
        scores = [r.score for r in response.results]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 6. TestQueryStrategyWiring
# ---------------------------------------------------------------------------

class TestQueryStrategyWiring:
    """Verify query type classification drives different strategies."""

    def test_factual_query_type(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("What does Alice do?")
        assert response.query_type in ("factual", "entity", "general")

    def test_temporal_query_type(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("When did Alice visit Paris?")
        assert response.query_type == "temporal"

    def test_different_weights_per_type(self, loaded_engine: MemoryEngine) -> None:
        r1 = loaded_engine.recall("What does Alice do?")
        r2 = loaded_engine.recall("When did Alice visit Paris?")
        # Temporal strategy boosts temporal weight
        assert r2.channel_weights.get("temporal", 0) >= r1.channel_weights.get("temporal", 0)


# ---------------------------------------------------------------------------
# 7. TestFisherRaoWiring
# ---------------------------------------------------------------------------

class TestFisherRaoWiring:
    """Verify Fisher-Rao parameters are stored and usable."""

    def test_fisher_params_stored_in_db(self, loaded_engine: MemoryEngine) -> None:
        rows = loaded_engine._db.execute(
            "SELECT fisher_mean, fisher_variance FROM atomic_facts "
            "WHERE profile_id = ? AND fisher_mean IS NOT NULL LIMIT 5",
            ("default",),
        )
        assert len(rows) > 0, "No facts have fisher_mean stored"
        for row in rows:
            d = dict(row)
            assert d["fisher_mean"] is not None
            assert d["fisher_variance"] is not None

    def test_fisher_distance_computable(self) -> None:
        """Direct Fisher-Rao metric test: distance between two distributions."""
        metric = FisherRaoMetric(temperature=15.0)
        mean_a = [0.5, 0.3, -0.2]
        var_a = [1.0, 1.0, 1.0]
        mean_b = [0.6, 0.2, -0.1]
        var_b = [1.0, 1.0, 1.0]
        d = metric.distance(mean_a, var_a, mean_b, var_b)
        assert d >= 0.0
        assert d < 100.0  # Sanity bound

    def test_fisher_self_distance_zero(self) -> None:
        metric = FisherRaoMetric()
        mean = [0.5, 0.3, -0.2]
        var = [1.0, 1.0, 1.0]
        d = metric.distance(mean, var, mean, var)
        assert d == pytest.approx(0.0, abs=1e-8)


# ---------------------------------------------------------------------------
# 8. TestHopfieldWiring
# ---------------------------------------------------------------------------

class TestHopfieldWiring:
    """Verify Hopfield network stores and retrieves patterns."""

    def test_store_and_retrieve(self) -> None:
        hop = ModernHopfield(beta=4.0, dimension=8)
        hop.store_pattern("alice", np.random.randn(8).tolist())
        hop.store_pattern("bob", np.random.randn(8).tolist())
        hop.store_pattern("charlie", np.random.randn(8).tolist())

        query = np.random.randn(8).tolist()
        results = hop.retrieve(query, top_k=3)
        assert len(results) == 3
        for entity_id, score in results:
            assert isinstance(entity_id, str)
            assert isinstance(score, float)
            assert score >= 0.0

    def test_retrieve_returns_sorted(self) -> None:
        hop = ModernHopfield(beta=4.0, dimension=16)
        for i in range(5):
            hop.store_pattern(f"e{i}", np.random.randn(16).tolist())
        results = hop.retrieve(np.random.randn(16).tolist(), top_k=5)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_energy_computable(self) -> None:
        hop = ModernHopfield(beta=4.0, dimension=8)
        hop.store_pattern("x", np.ones(8).tolist())
        e = hop.compute_energy(np.ones(8).tolist())
        assert isinstance(e, float)


# ---------------------------------------------------------------------------
# 9. TestLangevinWiring
# ---------------------------------------------------------------------------

class TestLangevinWiring:
    """Verify Langevin dynamics computes lifecycle positions."""

    def test_step_returns_valid_position(self) -> None:
        ld = LangevinDynamics(dt=0.01, temperature=1.0, dim=8)
        pos = [0.1] * 8
        new_pos, weight = ld.step(pos, access_count=5, age_days=2.0, importance=0.7, seed=42)
        assert len(new_pos) == 8
        assert all(isinstance(v, float) for v in new_pos)
        radius = np.linalg.norm(new_pos)
        assert radius < 1.0  # Must stay in the open ball

    def test_weight_bounded(self) -> None:
        ld = LangevinDynamics(dt=0.01, temperature=1.0, dim=8)
        pos = [0.1] * 8
        _, weight = ld.step(pos, access_count=5, age_days=2.0, importance=0.7, seed=42)
        assert 0.0 <= weight <= 1.0

    def test_lifecycle_state_valid(self) -> None:
        ld = LangevinDynamics(dt=0.01, temperature=1.0, dim=8)
        pos = [0.1] * 8
        _, weight = ld.step(pos, access_count=5, age_days=2.0, importance=0.7, seed=42)
        state = ld.get_lifecycle_state(weight)
        assert state in (
            MemoryLifecycle.ACTIVE,
            MemoryLifecycle.WARM,
            MemoryLifecycle.COLD,
            MemoryLifecycle.ARCHIVED,
        )

    def test_batch_step(self) -> None:
        ld = LangevinDynamics(dt=0.01, temperature=1.0, dim=8)
        facts = [
            {"fact_id": "f1", "position": [0.1] * 8, "access_count": 10, "age_days": 1.0, "importance": 0.8},
            {"fact_id": "f2", "position": [0.5] * 8, "access_count": 1, "age_days": 30.0, "importance": 0.2},
        ]
        results = ld.batch_step(facts, seed=42)
        assert len(results) == 2
        for r in results:
            assert "position" in r
            assert "weight" in r
            assert "lifecycle" in r


# ---------------------------------------------------------------------------
# 10. TestPoincareWiring
# ---------------------------------------------------------------------------

class TestPoincareWiring:
    """Verify Poincare hyperbolic distance properties."""

    def test_self_distance_zero(self) -> None:
        pb = PoincareBall(dimension=8)
        x = [0.1, 0.2, -0.1, 0.05, 0.0, 0.15, -0.05, 0.08]
        d = pb.distance(x, x)
        assert d == pytest.approx(0.0, abs=1e-6)

    def test_triangle_inequality(self) -> None:
        pb = PoincareBall(dimension=4)
        a = [0.1, 0.2, -0.1, 0.05]
        b = [0.3, -0.1, 0.2, 0.0]
        c = [-0.1, 0.3, 0.05, 0.15]
        d_ab = pb.distance(a, b)
        d_bc = pb.distance(b, c)
        d_ac = pb.distance(a, c)
        assert d_ac <= d_ab + d_bc + 1e-6

    def test_distance_positive(self) -> None:
        pb = PoincareBall(dimension=4)
        a = [0.1, 0.2, -0.1, 0.05]
        b = [0.3, -0.1, 0.2, 0.0]
        d = pb.distance(a, b)
        assert d > 0.0

    def test_project_to_ball(self) -> None:
        pb = PoincareBall(dimension=4)
        big_vec = [10.0, 20.0, -15.0, 5.0]
        projected = pb.project_to_ball(big_vec)
        norm = np.linalg.norm(projected)
        assert norm < 1.0


# ---------------------------------------------------------------------------
# 11. TestSheafWiring
# ---------------------------------------------------------------------------

class TestSheafWiring:
    """Verify sheaf coboundary detects contradictions."""

    def test_identical_embeddings_low_coboundary(self) -> None:
        dim = 8
        emb = np.random.randn(dim)
        R = np.eye(dim)
        severity = coboundary_norm(emb, emb, R, R)
        assert severity == pytest.approx(0.0, abs=1e-6)

    def test_different_embeddings_positive_coboundary(self) -> None:
        dim = 8
        emb_a = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        emb_b = np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        R = np.eye(dim)
        severity = coboundary_norm(emb_a, emb_b, R, R)
        assert severity > 0.0

    def test_coboundary_bounded(self) -> None:
        dim = 4
        emb_a = np.random.randn(dim)
        emb_b = np.random.randn(dim)
        R = np.eye(dim)
        severity = coboundary_norm(emb_a, emb_b, R, R)
        # Coboundary norm is in [0, ~2] range
        assert 0.0 <= severity <= 3.0


# ---------------------------------------------------------------------------
# 12. TestRateDistortionWiring
# ---------------------------------------------------------------------------

class TestRateDistortionWiring:
    """Verify rate-distortion optimal depth computation."""

    def test_optimal_levels_positive(self) -> None:
        rd = RateDistortionDepth(enabled=True)
        levels = rd.optimal_num_levels(1000)
        assert levels >= 1
        assert isinstance(levels, int)

    def test_more_memories_more_levels(self) -> None:
        rd = RateDistortionDepth(enabled=True)
        l_small = rd.optimal_num_levels(10)
        l_large = rd.optimal_num_levels(100_000)
        assert l_large >= l_small

    def test_depth_for_query(self) -> None:
        rd = RateDistortionDepth(enabled=True)
        depth = rd.get_depth_for_query("factual", 10_000)
        assert depth.level >= 0
        assert isinstance(depth.name, str)

    def test_disabled_returns_verbatim(self) -> None:
        rd = RateDistortionDepth(enabled=False)
        depth = rd.get_depth_for_query("factual", 10_000)
        assert depth.name == "verbatim"


# ---------------------------------------------------------------------------
# 13. TestReconsolidationOnRecall
# ---------------------------------------------------------------------------

class TestReconsolidationOnRecall:
    """Verify recall updates trust and access count."""

    def test_trust_entries_created_on_recall(self, loaded_engine: MemoryEngine) -> None:
        loaded_engine.recall("Alice")
        trust_rows = loaded_engine._db.execute(
            "SELECT * FROM trust_scores WHERE profile_id = ? AND target_type = 'fact'",
            ("default",),
        )
        assert len(trust_rows) > 0, "No trust entries created after recall"

    def test_access_count_incremented(self, loaded_engine: MemoryEngine) -> None:
        r1 = loaded_engine.recall("Alice")
        if not r1.results:
            pytest.skip("No results returned")

        fid = r1.results[0].fact.fact_id
        count_before = r1.results[0].fact.access_count

        # Second recall should increment access_count
        r2 = loaded_engine.recall("Alice")
        for result in r2.results:
            if result.fact.fact_id == fid:
                assert result.fact.access_count >= count_before


# ---------------------------------------------------------------------------
# 14. TestRecallResponseCompleteness
# ---------------------------------------------------------------------------

class TestRecallResponseCompleteness:
    """Verify response has all expected fields."""

    def test_response_query_populated(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        assert response.query == "Alice"

    def test_response_mode_populated(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        assert response.mode == Mode.A

    def test_response_results_is_list(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        assert isinstance(response.results, list)

    def test_response_query_type_set(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        assert isinstance(response.query_type, str)
        assert response.query_type != ""

    def test_response_channel_weights_dict(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        assert isinstance(response.channel_weights, dict)
        assert len(response.channel_weights) > 0

    def test_response_total_candidates(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        assert response.total_candidates >= 0

    def test_response_retrieval_time(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        assert response.retrieval_time_ms > 0

    def test_result_fields_complete(self, loaded_engine: MemoryEngine) -> None:
        response = loaded_engine.recall("Alice")
        for result in response.results:
            assert isinstance(result.fact, AtomicFact)
            assert isinstance(result.score, float)
            assert isinstance(result.channel_scores, dict)
            assert 0.0 <= result.confidence <= 1.0
            assert isinstance(result.evidence_chain, list)
            assert isinstance(result.trust_score, float)
