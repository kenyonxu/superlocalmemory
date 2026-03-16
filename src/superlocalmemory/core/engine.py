# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Main Memory Engine.

Orchestrates the full memory lifecycle: store, encode, retrieve.
Single entry point for all memory operations.
Profile-scoped. Mode-aware (A/B/C).

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import logging
from typing import Any

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.modes import get_capabilities
from superlocalmemory.storage.models import (
    AtomicFact, FactType, MemoryRecord, Mode, RecallResponse,
)

logger = logging.getLogger(__name__)

from superlocalmemory.core.hooks import HookRegistry


class MemoryEngine:
    """Main orchestrator for the SuperLocalMemory V3 memory system.

    Wires encoding (fact extraction, entity resolution, graph building,
    consolidation) with retrieval (4-channel search, RRF fusion,
    reranking) and all supporting layers (trust, learning, compliance).

    Usage::

        config = SLMConfig.for_mode(Mode.A)
        engine = MemoryEngine(config)
        engine.store("Alice went to Paris last summer", session_id="s1")
        response = engine.recall("Where did Alice go?")
    """

    def __init__(self, config: SLMConfig) -> None:
        self._config = config
        self._caps = get_capabilities(config.mode)
        self._profile_id = config.active_profile
        self._initialized = False

        self._db = None
        self._embedder = None
        self._llm = None
        self._fact_extractor = None
        self._entity_resolver = None
        self._temporal_parser = None
        self._type_router = None
        self._graph_builder = None
        self._consolidator = None
        self._observation_builder = None
        self._scene_builder = None
        self._entropy_gate = None
        self._retrieval_engine = None
        self._trust_scorer = None
        self._ann_index = None
        self._sheaf_checker = None
        self._provenance = None
        self._adaptive_learner = None
        self._compliance_checker = None
        self._hooks = HookRegistry()

    def initialize(self) -> None:
        """Initialize all components. Call once before use."""
        if self._initialized:
            return

        from superlocalmemory.storage import schema
        from superlocalmemory.storage.database import DatabaseManager
        from superlocalmemory.core.embeddings import EmbeddingService
        from superlocalmemory.llm.backbone import LLMBackbone

        self._db = DatabaseManager(self._config.db_path)
        self._db.initialize(schema)
        try:
            emb = EmbeddingService(self._config.embedding)
            self._embedder = emb if emb.is_available else None
        except Exception as exc:
            logger.warning("Embeddings unavailable (%s). BM25-only mode.", exc)
            self._embedder = None

        if self._caps.llm_fact_extraction:
            self._llm = LLMBackbone(self._config.llm)
            if not self._llm.is_available():
                logger.warning("LLM not available. Falling back to Mode A extraction.")
                self._llm = None

        from superlocalmemory.trust.scorer import TrustScorer
        from superlocalmemory.trust.provenance import ProvenanceTracker
        from superlocalmemory.learning.adaptive import AdaptiveLearner
        from superlocalmemory.compliance.eu_ai_act import EUAIActChecker

        self._trust_scorer = TrustScorer(self._db)

        self._init_encoding()
        self._init_retrieval()

        self._provenance = ProvenanceTracker(self._db)
        self._adaptive_learner = AdaptiveLearner(self._db)
        self._compliance_checker = EUAIActChecker()

        # Wire lifecycle hooks
        self._wire_hooks()

        self._initialized = True
        logger.info("MemoryEngine initialized: mode=%s profile=%s",
                     self._config.mode.value, self._profile_id)

    def store(
        self,
        content: str,
        session_id: str = "",
        session_date: str | None = None,
        speaker: str = "",
        role: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        """Store content and extract structured facts. Returns fact_ids."""
        self._ensure_init()

        # Pre-operation hooks (trust gate, ABAC, rate limiter)
        hook_ctx = {"operation": "store", "agent_id": metadata.get("agent_id", "unknown") if metadata else "unknown",
                     "profile_id": self._profile_id, "content_preview": content[:100]}
        self._hooks.run_pre("store", hook_ctx)

        if self._entropy_gate and not self._entropy_gate.should_pass(content):
            return []

        from superlocalmemory.encoding.temporal_parser import TemporalParser
        parser = self._temporal_parser or TemporalParser()
        parsed_date = parser.parse_session_date(session_date) if session_date else None

        record = MemoryRecord(
            profile_id=self._profile_id, content=content,
            session_id=session_id, speaker=speaker, role=role,
            session_date=parsed_date, metadata=metadata or {},
        )
        self._db.store_memory(record)

        facts = self._fact_extractor.extract_facts(
            turns=[content], session_id=session_id,
            session_date=parsed_date, speaker_a=speaker,
        )
        if not facts:
            return []

        if self._type_router:
            facts = self._type_router.route_facts(facts)

        stored_ids: list[str] = []
        for fact in facts:
            fact = self._enrich_fact(fact, record)

            if self._consolidator:
                action = self._consolidator.consolidate(fact, self._profile_id)
                if action.action_type.value == "noop":
                    continue

                # Opinion confidence tracking: reinforce or decay
                # When a new opinion aligns with existing, boost confidence.
                # When contradicted (supersede), reduce old fact's confidence.
                if fact.fact_type == FactType.OPINION and action.action_type.value == "update":
                    try:
                        existing = self._db.get_fact(action.new_fact_id)
                        if existing and existing.fact_type == FactType.OPINION:
                            new_conf = min(1.0, existing.confidence + 0.1)
                            self._db.update_fact(action.new_fact_id, {"confidence": new_conf})
                    except Exception:
                        pass
                elif fact.fact_type == FactType.OPINION and action.action_type.value == "supersede":
                    try:
                        old_id = getattr(action, "old_fact_id", None)
                        if old_id:
                            old_fact = self._db.get_fact(old_id)
                            if old_fact:
                                new_conf = max(0.0, old_fact.confidence - 0.2)
                                self._db.update_fact(old_id, {"confidence": new_conf})
                    except Exception:
                        pass

                if action.action_type.value in ("update", "supersede"):
                    # Run post-processing on updated facts
                    updated_fact = self._db.get_fact(action.new_fact_id)
                    if updated_fact:
                        if self._graph_builder:
                            self._graph_builder.build_edges(updated_fact, self._profile_id)
                        if self._observation_builder:
                            for eid in updated_fact.canonical_entities:
                                self._observation_builder.update_profile(
                                    eid, updated_fact, self._profile_id,
                                )
                    stored_ids.append(action.new_fact_id)
                    continue
                # ADD case: consolidator already stored the fact (F8 fix)
                # Fall through to post-processing below
            else:
                self._db.store_fact(fact)

            stored_ids.append(fact.fact_id)

            if fact.embedding and self._ann_index:
                self._ann_index.add(fact.fact_id, fact.embedding)
            if self._graph_builder:
                self._graph_builder.build_edges(fact, self._profile_id)

            # Sheaf consistency check (runs after edges exist)
            # Cap edge check to prevent O(N^2) hang on large graphs
            if (self._sheaf_checker
                    and fact.embedding
                    and fact.canonical_entities):
                from superlocalmemory.storage.models import EdgeType, GraphEdge
                try:
                    edges_for_fact = self._db.get_edges_for_node(
                        fact.fact_id, self._profile_id,
                    )
                    # Only run sheaf if edge count is manageable
                    # At 18K+ edges, the coboundary computation becomes O(N*dim^2)
                    if len(edges_for_fact) < self._config.math.sheaf_max_edges_per_check:
                        contradictions = self._sheaf_checker.check_consistency(
                            fact, self._profile_id,
                        )
                        for c in contradictions:
                            if c.severity > 0.45:
                                edge = GraphEdge(
                                    profile_id=self._profile_id,
                                    source_id=fact.fact_id,
                                    target_id=c.fact_id_b,
                                    edge_type=EdgeType.SUPERSEDES,
                                    weight=c.severity,
                                )
                                self._db.store_edge(edge)
                except Exception as exc:
                    logger.debug("Sheaf check skipped: %s", exc)

            if self._observation_builder:
                for eid in fact.canonical_entities:
                    self._observation_builder.update_profile(eid, fact, self._profile_id)

            # Increment fact_count for each linked canonical entity
            for eid in fact.canonical_entities:
                try:
                    self._db.increment_entity_fact_count(eid)
                except Exception:
                    pass  # Non-critical — entity may have been deleted
            if self._scene_builder:
                self._scene_builder.assign_to_scene(fact, self._profile_id)

            # Populate temporal_events for temporal retrieval
            has_dates = (fact.observation_date or fact.referenced_date
                         or fact.interval_start)
            if fact.canonical_entities and has_dates:
                from superlocalmemory.storage.models import TemporalEvent
                for eid in fact.canonical_entities:
                    event = TemporalEvent(
                        profile_id=self._profile_id, entity_id=eid,
                        fact_id=fact.fact_id,
                        observation_date=fact.observation_date,
                        referenced_date=fact.referenced_date,
                        interval_start=fact.interval_start,
                        interval_end=fact.interval_end,
                        description=fact.content[:200],
                    )
                    self._db.store_temporal_event(event)

            # Foresight: extract time-bounded predictions
            try:
                from superlocalmemory.encoding.foresight import extract_foresight_signals
                from superlocalmemory.storage.models import TemporalEvent as _TE
                foresight_signals = extract_foresight_signals(fact)
                for sig in foresight_signals:
                    f_event = _TE(
                        profile_id=self._profile_id,
                        entity_id=sig.get("entity_id", ""),
                        fact_id=fact.fact_id,
                        interval_start=sig.get("start_time"),
                        interval_end=sig.get("end_time"),
                        description=sig.get("description", ""),
                    )
                    self._db.store_temporal_event(f_event)
            except Exception as exc:
                logger.debug("Foresight extraction: %s", exc)

            # Persist BM25 tokens at ingestion
            bm25 = getattr(self._retrieval_engine, '_bm25', None) if self._retrieval_engine else None
            if bm25:
                bm25.add(fact.fact_id, fact.content, self._profile_id)

            # Record provenance for data lineage (EU AI Act Art. 10)
            if self._provenance:
                try:
                    self._provenance.record(
                        fact_id=fact.fact_id,
                        profile_id=self._profile_id,
                        source_type="store",
                        source_id=session_id,
                        created_by=speaker or "unknown",
                    )
                except Exception:
                    pass

        logger.info("Stored %d facts (session=%s)", len(stored_ids), session_id)

        # Post-operation hooks (audit, trust signal, event bus)
        hook_ctx["fact_ids"] = stored_ids
        hook_ctx["fact_count"] = len(stored_ids)
        self._hooks.run_post("store", hook_ctx)

        return stored_ids

    def recall(
        self, query: str, profile_id: str | None = None,
        mode: Mode | None = None, limit: int = 20,
        agent_id: str = "unknown",
    ) -> RecallResponse:
        """Recall relevant facts for a query.

        Pipeline: retrieval → agentic sufficiency (if configured) → post-recall updates.
        Agentic sufficiency (sufficiency check): triggers 2-round re-retrieval when
        initial results are insufficient. Mode C uses LLM judgment; Mode A uses
        heuristic alias expansion.
        """
        self._ensure_init()

        # Pre-operation hooks
        hook_ctx = {"operation": "recall", "agent_id": agent_id,
                     "profile_id": profile_id or self._profile_id, "query_preview": query[:100]}
        self._hooks.run_pre("recall", hook_ctx)

        pid = profile_id or self._profile_id
        m = mode or self._config.mode

        response = self._retrieval_engine.recall(query, pid, m, limit)

        # Agentic sufficiency verification
        # Only trigger when: (a) configured rounds > 0, (b) results look weak
        agentic_rounds = self._config.retrieval.agentic_max_rounds
        if agentic_rounds > 0 and response.results:
            max_score = max((r.score for r in response.results), default=0.0)
            should_trigger = (
                max_score < self._config.retrieval.agentic_confidence_threshold
                or response.query_type == "multi_hop"
                or len(response.results) < 3
            )
            if should_trigger:
                try:
                    from superlocalmemory.retrieval.agentic import AgenticRetriever
                    agentic = AgenticRetriever(
                        confidence_threshold=self._config.retrieval.agentic_confidence_threshold,
                        db=self._db,
                    )
                    enhanced_facts = agentic.retrieve(
                        query=query, profile_id=pid,
                        retrieval_engine=self._retrieval_engine,
                        llm=self._llm,
                        top_k=limit,
                        query_type=response.query_type,
                    )
                    # Replace response results with enhanced facts if we got more
                    if len(enhanced_facts) > len(response.results):
                        from superlocalmemory.storage.models import RetrievalResult
                        enhanced_results = []
                        for i, f in enumerate(enhanced_facts):
                            # Look up real trust score for agentic results
                            fact_trust = 0.5
                            if self._trust_scorer:
                                try:
                                    fact_trust = self._trust_scorer.get_fact_trust(
                                        f.fact_id, pid,
                                    )
                                except Exception:
                                    pass
                            enhanced_results.append(RetrievalResult(
                                fact=f, score=1.0 / (i + 1),
                                channel_scores={"agentic": 1.0},
                                confidence=f.confidence,
                                evidence_chain=["agentic_round_2"],
                                trust_score=fact_trust,
                            ))
                        response = RecallResponse(
                            query=query, mode=m, results=enhanced_results[:limit],
                            query_type=response.query_type,
                            channel_weights=response.channel_weights,
                            total_candidates=response.total_candidates + len(enhanced_facts),
                            retrieval_time_ms=response.retrieval_time_ms,
                        )
                except Exception as exc:
                    logger.debug("Agentic sufficiency skipped: %s", exc)

        # Reconsolidation: access updates trust + count (neuroscience principle)
        if self._trust_scorer:
            for r in response.results:
                self._trust_scorer.update_on_access("fact", r.fact.fact_id, pid)

        # Fisher Bayesian update on recall
        q_emb = self._embedder.embed(query) if self._embedder else None
        q_var_arr = None
        if self._embedder and q_emb:
            _, q_var_list = self._embedder.compute_fisher_params(q_emb)
            import numpy as _np
            q_var_arr = _np.array(q_var_list, dtype=_np.float64)

        for r in response.results:
            updates: dict[str, object] = {
                "access_count": r.fact.access_count + 1,
            }
            # Bayesian variance narrowing after 3+ accesses
            if (q_var_arr is not None
                    and r.fact.fisher_variance
                    and len(r.fact.fisher_variance) == len(q_var_arr)
                    and r.fact.access_count >= 3):
                import numpy as _np
                f_var = _np.array(r.fact.fisher_variance, dtype=_np.float64)
                # Conjugate Gaussian update: 1/new_var = 1/f_var + 1/q_var
                new_var = 1.0 / (1.0 / _np.maximum(f_var, 0.05) + 1.0 / _np.maximum(q_var_arr, 0.05))
                new_var = _np.clip(new_var, 0.05, 2.0)
                updates["fisher_variance"] = new_var.tolist()

            self._db.update_fact(r.fact.fact_id, updates)

        # Post-operation hooks (audit, trust signal, learning)
        hook_ctx["result_count"] = len(response.results)
        hook_ctx["query_type"] = response.query_type
        self._hooks.run_post("recall", hook_ctx)

        return response

    def store_fact_direct(self, fact: AtomicFact) -> str:
        """Store a pre-built fact with full enrichment.

        Ensures embedding, Fisher params, canonical entities, BM25 tokens,
        and graph edges are all populated — even for auxiliary data.
        Creates a parent memory record to satisfy FK constraint.
        """
        self._ensure_init()

        # Create parent memory record (FK: atomic_facts.memory_id → memories.memory_id)
        if not fact.memory_id:
            from superlocalmemory.storage.models import _new_id
            record = MemoryRecord(
                profile_id=self._profile_id,
                content=fact.content[:500],
                session_id=fact.session_id,
            )
            self._db.store_memory(record)
            fact.memory_id = record.memory_id

        if not fact.embedding and self._embedder:
            fact.embedding = self._embedder.embed(fact.content)
            if fact.embedding:
                fact.fisher_mean, fact.fisher_variance = (
                    self._embedder.compute_fisher_params(fact.embedding)
                )
        if self._entity_resolver and fact.entities:
            canonical = self._entity_resolver.resolve(
                fact.entities, self._profile_id,
            )
            fact.canonical_entities = list(canonical.values())
        self._db.store_fact(fact)
        if fact.embedding and self._ann_index:
            self._ann_index.add(fact.fact_id, fact.embedding)
        if self._graph_builder:
            self._graph_builder.build_edges(fact, self._profile_id)
        # BM25 indexing
        bm25 = getattr(self._retrieval_engine, '_bm25', None) if self._retrieval_engine else None
        if bm25:
            bm25.add(fact.fact_id, fact.content, self._profile_id)
        return fact.fact_id

    def create_speaker_entities(self, speaker_a: str, speaker_b: str) -> None:
        """Pre-create canonical entities for conversation speakers."""
        self._ensure_init()
        if self._entity_resolver:
            self._entity_resolver.create_speaker_entities(
                speaker_a, speaker_b, self._profile_id,
            )

    def close_session(self, session_id: str) -> int:
        """Create session-level temporal summary for session-level retrieval.

        Aggregates facts from a completed session into temporal_events
        with session scope. Enables temporal queries like "What happened
        in session 3?"

        Returns number of session summary events created.
        """
        self._ensure_init()
        from superlocalmemory.storage.models import TemporalEvent

        facts = self._db.get_all_facts(self._profile_id)
        session_facts = [f for f in facts if f.session_id == session_id]
        if not session_facts:
            return 0

        # Group by entity for session-level summaries
        entity_facts: dict[str, list[AtomicFact]] = {}
        for f in session_facts:
            for eid in f.canonical_entities:
                entity_facts.setdefault(eid, []).append(f)

        count = 0
        session_date = session_facts[0].observation_date or ""
        for eid, efacts in entity_facts.items():
            summary_parts = [f.content[:80] for f in efacts[:5]]
            summary = f"Session {session_id}: " + "; ".join(summary_parts)
            event = TemporalEvent(
                profile_id=self._profile_id,
                entity_id=eid,
                fact_id=efacts[0].fact_id,
                observation_date=session_date,
                description=summary[:500],
            )
            self._db.store_temporal_event(event)
            count += 1

        logger.info(
            "Session %s closed: %d summary events for %d facts",
            session_id, count, len(session_facts),
        )
        return count

    def close(self) -> None:
        self._initialized = False

    @property
    def profile_id(self) -> str:
        return self._profile_id

    @profile_id.setter
    def profile_id(self, value: str) -> None:
        self._profile_id = value

    @property
    def fact_count(self) -> int:
        self._ensure_init()
        return self._db.get_fact_count(self._profile_id)

    # -- Internal ----------------------------------------------------------

    def _ensure_init(self) -> None:
        if not self._initialized:
            self.initialize()

    def _init_encoding(self) -> None:
        from superlocalmemory.encoding.fact_extractor import FactExtractor
        from superlocalmemory.encoding.entity_resolver import EntityResolver
        from superlocalmemory.encoding.temporal_parser import TemporalParser
        from superlocalmemory.encoding.type_router import TypeRouter
        from superlocalmemory.encoding.graph_builder import GraphBuilder
        from superlocalmemory.encoding.consolidator import MemoryConsolidator
        from superlocalmemory.encoding.observation_builder import ObservationBuilder
        from superlocalmemory.encoding.scene_builder import SceneBuilder
        from superlocalmemory.encoding.entropy_gate import EntropyGate
        from superlocalmemory.retrieval.ann_index import ANNIndex

        self._ann_index = ANNIndex(dimension=self._config.embedding.dimension)
        self._fact_extractor = FactExtractor(
            config=self._config.encoding, llm=self._llm,
            embedder=self._embedder, mode=self._config.mode,
        )
        self._entity_resolver = EntityResolver(self._db, self._llm)
        self._temporal_parser = TemporalParser()
        self._type_router = TypeRouter(
            mode=self._config.mode, embedder=self._embedder, llm=self._llm,
        )
        self._graph_builder = GraphBuilder(self._db, self._ann_index)
        self._consolidator = MemoryConsolidator(
            self._db, self._embedder, self._llm, self._config.encoding,
        )
        self._observation_builder = ObservationBuilder(self._db)
        self._scene_builder = SceneBuilder(self._db, self._embedder)
        self._entropy_gate = EntropyGate(
            self._embedder, self._config.encoding.entropy_threshold,
        )

        # Wire Sheaf consistency checker
        if self._config.math.sheaf_at_encoding:
            from superlocalmemory.math.sheaf import SheafConsistencyChecker
            self._sheaf_checker = SheafConsistencyChecker(
                self._db, self._config.math.sheaf_contradiction_threshold,
            )

    def _init_retrieval(self) -> None:
        from superlocalmemory.retrieval.engine import RetrievalEngine
        from superlocalmemory.retrieval.semantic_channel import SemanticChannel
        from superlocalmemory.retrieval.bm25_channel import BM25Channel
        from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
        from superlocalmemory.retrieval.temporal_channel import TemporalChannel
        from superlocalmemory.retrieval.reranker import CrossEncoderReranker
        from superlocalmemory.retrieval.profile_channel import ProfileChannel
        from superlocalmemory.retrieval.bridge_discovery import BridgeDiscovery

        channels: dict = {
            "semantic": SemanticChannel(
                self._db,
                fisher_temperature=self._config.math.fisher_temperature,
                embedder=self._embedder,
                fisher_mode=self._config.math.fisher_mode,
            ),
            "bm25": BM25Channel(self._db),
            "entity_graph": EntityGraphChannel(self._db, self._entity_resolver),
            "temporal": TemporalChannel(self._db),
        }
        reranker = None
        if self._config.retrieval.use_cross_encoder:
            reranker = CrossEncoderReranker(self._config.retrieval.cross_encoder_model)

        profile_ch = ProfileChannel(self._db)
        bridge = BridgeDiscovery(self._db)

        self._retrieval_engine = RetrievalEngine(
            db=self._db, config=self._config.retrieval, channels=channels,
            embedder=self._embedder, reranker=reranker,
            base_weights=self._config.channel_weights,
            profile_channel=profile_ch,
            bridge_discovery=bridge,
            trust_scorer=self._trust_scorer,
        )

    def _wire_hooks(self) -> None:
        """Wire trust, compliance, and event bus hooks into engine lifecycle."""
        # -- Pre-store hooks (synchronous, can reject) --
        if self._trust_scorer:
            from superlocalmemory.trust.gate import TrustGate
            gate = TrustGate(self._trust_scorer)
            self._hooks.register_pre("store", lambda ctx: gate.check_write(
                ctx.get("agent_id", "unknown"), ctx.get("profile_id", self._profile_id)))
            self._hooks.register_pre("delete", lambda ctx: gate.check_delete(
                ctx.get("agent_id", "unknown"), ctx.get("profile_id", self._profile_id)))

        # -- Post-store hooks (async, never block) --
        if self._trust_scorer:
            self._hooks.register_post("store", lambda ctx: self._trust_scorer.record_signal(
                ctx.get("agent_id", "unknown"), ctx.get("profile_id", self._profile_id), "store_success"))
            self._hooks.register_post("recall", lambda ctx: self._trust_scorer.record_signal(
                ctx.get("agent_id", "unknown"), ctx.get("profile_id", self._profile_id), "recall_hit"))

        # -- Burst detection via SignalRecorder --
        try:
            from superlocalmemory.trust.signals import SignalRecorder
            self._signal_recorder = SignalRecorder(self._db)
            self._hooks.register_post("store", lambda ctx: self._signal_recorder.record(
                ctx.get("agent_id", "unknown"), ctx.get("profile_id", self._profile_id), "store_success"))
        except Exception:
            self._signal_recorder = None

        # -- Tamper-proof audit chain (all operations logged with hash chain) --
        try:
            from superlocalmemory.compliance.audit import AuditChain
            audit_path = self._config.db_path.parent / "audit_chain.db"
            self._audit_chain = AuditChain(audit_path)
            for op in ("store", "recall", "delete"):
                self._hooks.register_post(op, lambda ctx, _op=op: self._audit_chain.log(
                    operation=_op,
                    agent_id=ctx.get("agent_id", "unknown"),
                    profile_id=ctx.get("profile_id", self._profile_id),
                    content_hash=ctx.get("content_hash", ""),
                ))
        except Exception:
            self._audit_chain = None

    def _enrich_fact(self, fact: AtomicFact, record: MemoryRecord) -> AtomicFact:
        """Enrich fact with embeddings, entities, temporal, emotional data."""
        from superlocalmemory.encoding.emotional import tag_emotion, emotional_importance_boost
        from superlocalmemory.encoding.signal_inference import infer_signal

        embedding = self._embedder.embed(fact.content) if self._embedder else None
        fisher_mean, fisher_variance = (None, None)
        if self._embedder and embedding:
            fisher_mean, fisher_variance = self._embedder.compute_fisher_params(embedding)

        canonical = {}
        if self._entity_resolver and fact.entities:
            canonical = self._entity_resolver.resolve(fact.entities, self._profile_id)

        temporal = {}
        if self._temporal_parser:
            temporal = self._temporal_parser.extract_dates_from_text(fact.content)

        emotion = tag_emotion(fact.content)
        signal = infer_signal(fact.content)

        return AtomicFact(
            fact_id=fact.fact_id, memory_id=record.memory_id,
            profile_id=self._profile_id, content=fact.content,
            fact_type=fact.fact_type, entities=fact.entities,
            canonical_entities=list(canonical.values()),
            observation_date=fact.observation_date or record.session_date,
            referenced_date=fact.referenced_date or temporal.get("referenced_date"),
            interval_start=fact.interval_start or temporal.get("interval_start"),
            interval_end=fact.interval_end or temporal.get("interval_end"),
            confidence=fact.confidence,
            importance=min(1.0, fact.importance + emotional_importance_boost(emotion)),
            evidence_count=fact.evidence_count,
            source_turn_ids=fact.source_turn_ids, session_id=record.session_id,
            embedding=embedding, fisher_mean=fisher_mean, fisher_variance=fisher_variance,
            emotional_valence=emotion.valence, emotional_arousal=emotion.arousal,
            signal_type=signal, created_at=fact.created_at,
        )
