# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Configuration.

Unified configuration with Mode A/B/C capability matrix.
Clean — zero dead options, every config has a consumer.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from superlocalmemory.storage.models import Mode


# ---------------------------------------------------------------------------
# Default Paths
# ---------------------------------------------------------------------------

DEFAULT_BASE_DIR = Path.home() / ".superlocalmemory"
DEFAULT_DB_NAME = "memory.db"
DEFAULT_PROFILES_FILE = "profiles.json"


# ---------------------------------------------------------------------------
# Embedding Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding model configuration per mode."""

    model_name: str = "nomic-ai/nomic-embed-text-v1.5"
    dimension: int = 768
    # Azure / cloud settings (Mode C only)
    api_endpoint: str = ""
    api_key: str = ""
    api_version: str = "2024-02-01"
    deployment_name: str = ""

    @property
    def is_cloud(self) -> bool:
        return bool(self.api_endpoint)


# ---------------------------------------------------------------------------
# LLM Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMConfig:
    """LLM provider configuration per mode."""

    provider: str = ""             # "" = no LLM, "ollama", "azure", "openai", "anthropic"
    model: str = ""                # Model name/deployment
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.0       # Deterministic by default
    max_tokens: int = 4096
    timeout_seconds: float = 60.0

    @property
    def is_available(self) -> bool:
        return bool(self.provider)


# ---------------------------------------------------------------------------
# Channel Weights
# ---------------------------------------------------------------------------

@dataclass
class ChannelWeights:
    """Retrieval channel weights — 4 channels, query-adaptive."""

    # Entity-linked facts are high-precision matches that rank above BM25.
    semantic: float = 1.2
    bm25: float = 1.0
    entity_graph: float = 1.3
    temporal: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return {
            "semantic": self.semantic,
            "bm25": self.bm25,
            "entity_graph": self.entity_graph,
            "temporal": self.temporal,
        }


# ---------------------------------------------------------------------------
# Encoding Config
# ---------------------------------------------------------------------------

@dataclass
class EncodingConfig:
    """Configuration for the encoding (memory creation) pipeline."""

    # Fact extraction
    chunk_size: int = 10           # Conversation turns per extraction chunk
    max_facts_per_chunk: int = 5   # Max facts extracted per chunk
    min_fact_confidence: float = 0.3

    # Entity resolution
    entity_similarity_threshold: float = 0.85
    max_entity_candidates: int = 10

    # Graph construction
    semantic_edge_top_k: int = 5   # Top-K semantic edges per new fact
    temporal_edge_window_hours: int = 168  # 1 week

    # Consolidation
    consolidation_similarity_threshold: float = 0.85
    max_consolidation_candidates: int = 5

    # Entropy gate
    entropy_threshold: float = 0.95


# ---------------------------------------------------------------------------
# Retrieval Config
# ---------------------------------------------------------------------------

@dataclass
class RetrievalConfig:
    """Configuration for the retrieval (recall) pipeline."""

    # Fusion
    rrf_k: int = 60               # RRF smoothing constant (D116: k=60 for diversity)
    top_k: int = 20               # Final results to return

    # Per-channel
    semantic_top_k: int = 50      # ANN pre-filter candidates
    bm25_top_k: int = 50
    entity_graph_max_hops: int = 3
    temporal_proximity_days: int = 30

    # Reranking
    use_cross_encoder: bool = True
    cross_encoder_model: str = "BAAI/bge-reranker-v2-m3"

    # Agentic (Mode C only)
    agentic_max_rounds: int = 3
    agentic_confidence_threshold: float = 0.3

    # Spreading activation
    spreading_activation_decay: float = 0.7
    spreading_activation_threshold: float = 0.1

    # Trust weighting — apply Bayesian trust scores to retrieval ranking.
    # When enabled, each fact's score is multiplied by a trust weight in [0.5, 1.5].
    # Low-trust facts are demoted; high-trust facts are promoted.
    # Default trust = 1.0 (no effect when no trust data exists).
    use_trust_weighting: bool = True

    # Ablation channel control for experiments.
    # List of channel names to SKIP during retrieval (e.g., ["bm25", "entity_graph"]).
    # Used by s19_runner for ablation experiments. Empty = all channels active.
    disabled_channels: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Math Config
# ---------------------------------------------------------------------------

@dataclass
class MathConfig:
    """Configuration for mathematical layers."""

    # Fisher-Rao
    fisher_temperature: float = 15.0
    fisher_bayesian_update: bool = True
    # "simplified" = local Mahalanobis-like (fast, existing behaviour)
    # "full"       = Atkinson-Mitchell geodesic from FisherRaoMetric class
    fisher_mode: str = "simplified"

    # Langevin
    langevin_dt: float = 0.005
    langevin_temperature: float = 0.3
    langevin_persist_positions: bool = True
    langevin_weight_range: tuple[float, float] = (0.0, 1.0)

    # Hopfield
    hopfield_beta: float = 4.0

    # Sheaf (at encoding time, NOT retrieval)
    sheaf_at_encoding: bool = True
    sheaf_contradiction_threshold: float = 0.45
    # Max edges to check per fact during sheaf consistency.
    # At 18K+ edges, coboundary computation becomes O(N*dim^2) and hangs.
    # Facts with more edges than this skip sheaf check (still get contradiction
    # detection via consolidator UPDATE/SUPERSEDE path).
    sheaf_max_edges_per_check: int = 200

    # Rate-Distortion (production only, disabled for benchmarks)
    rate_distortion_enabled: bool = False


# ---------------------------------------------------------------------------
# Master Config
# ---------------------------------------------------------------------------

@dataclass
class SLMConfig:
    """Master configuration for SuperLocalMemory V3.

    Create via SLMConfig.for_mode(Mode.A) for mode-specific defaults.
    """

    mode: Mode = Mode.A
    base_dir: Path = DEFAULT_BASE_DIR
    db_path: Path | None = None    # Computed from base_dir if None
    active_profile: str = "default"

    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    channel_weights: ChannelWeights = field(default_factory=ChannelWeights)
    encoding: EncodingConfig = field(default_factory=EncodingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    math: MathConfig = field(default_factory=MathConfig)

    def __post_init__(self) -> None:
        if self.db_path is None:
            self.db_path = self.base_dir / DEFAULT_DB_NAME

    @classmethod
    def for_mode(
        cls,
        mode: Mode,
        base_dir: Path | None = None,
        *,
        llm_provider: str = "",
        llm_model: str = "",
        llm_api_key: str = "",
        llm_api_base: str = "",
        embedding_endpoint: str = "",
        embedding_key: str = "",
        embedding_deployment: str = "",
    ) -> SLMConfig:
        """Create config with mode-appropriate defaults."""
        _base = base_dir or DEFAULT_BASE_DIR

        if mode == Mode.A:
            return cls(
                mode=mode,
                base_dir=_base,
                embedding=EmbeddingConfig(
                    model_name="nomic-ai/nomic-embed-text-v1.5",
                    dimension=768,
                ),
                llm=LLMConfig(),  # No LLM
                retrieval=RetrievalConfig(
                    use_cross_encoder=True,  # Hotpatch: CE ON for Mode A with bge-reranker
                ),
                math=MathConfig(
                    rate_distortion_enabled=False,
                    sheaf_contradiction_threshold=0.45,  # 768d threshold
                ),
            )

        if mode == Mode.B:
            return cls(
                mode=mode,
                base_dir=_base,
                embedding=EmbeddingConfig(
                    model_name="nomic-ai/nomic-embed-text-v1.5",
                    dimension=768,
                ),
                llm=LLMConfig(
                    provider=llm_provider or "ollama",
                    model=llm_model or "phi3:mini",
                    api_base=llm_api_base or "http://localhost:11434",
                ),
                retrieval=RetrievalConfig(use_cross_encoder=True),
                math=MathConfig(rate_distortion_enabled=False),
            )

        # Mode C — FULL POWER, UNRESTRICTED
        return cls(
            mode=mode,
            base_dir=_base,
            embedding=EmbeddingConfig(
                model_name="text-embedding-3-large",
                dimension=3072,
                api_endpoint=embedding_endpoint,
                api_key=embedding_key,
                deployment_name=embedding_deployment,
            ),
            llm=LLMConfig(
                provider=llm_provider or "azure",
                model=llm_model or "gpt-4.1-mini",
                api_key=llm_api_key,
                api_base=llm_api_base,
            ),
            channel_weights=ChannelWeights(
                semantic=1.5,
                bm25=1.2,
                entity_graph=1.3,
                temporal=1.0,
            ),
            retrieval=RetrievalConfig(
                use_cross_encoder=True,
                semantic_top_k=80,
                agentic_max_rounds=2,  # EverMemOS 2-round
            ),
            math=MathConfig(
                rate_distortion_enabled=False,
                sheaf_contradiction_threshold=0.65,  # Higher for 3072d embeddings
            ),
        )
