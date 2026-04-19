# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Sleep-Time Consolidation Worker — background memory maintenance.

Runs periodically (every 6 hours or on-demand) to:
1. Decay confidence on unused facts (floor 0.1)
2. Deduplicate near-identical facts
3. Auto-retrain the adaptive ranker when signal threshold is met
4. Report consolidation stats

Inspired by: Letta's sleep-time compute, neuroscience memory consolidation.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ConsolidationWorker:
    """Background memory maintenance worker.

    Call `run()` periodically or via dashboard button.
    All operations are safe — they improve quality without losing data.
    """

    def __init__(self, memory_db: str | Path, learning_db: str | Path) -> None:
        self._memory_db = str(memory_db)
        self._learning_db = str(learning_db)

    def run(self, profile_id: str, dry_run: bool = False) -> dict:
        """Run full consolidation cycle. Returns stats."""
        stats = {
            "decayed": 0,
            "deduped": 0,
            "retrained": False,
            "signal_count": 0,
            "ranker_phase": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Confidence decay on unused facts
        try:
            from superlocalmemory.learning.signals import LearningSignals
            decayed = LearningSignals.decay_confidence(
                self._memory_db, profile_id, rate=0.001,
            )
            stats["decayed"] = decayed
            if not dry_run:
                logger.info("Confidence decay: %d facts affected", decayed)
        except Exception as exc:
            logger.debug("Decay failed: %s", exc)

        # 2. Deduplication (mark near-identical facts)
        try:
            deduped = self._deduplicate(profile_id, dry_run)
            stats["deduped"] = deduped
        except Exception as exc:
            logger.debug("Dedup failed: %s", exc)

        # 3. Generate behavioral patterns from memories
        try:
            patterns = self._generate_patterns(profile_id, dry_run)
            stats["patterns_generated"] = patterns
        except Exception as exc:
            logger.debug("Pattern generation failed: %s", exc)

        # 4. Recompute graph intelligence (v3.4.2: wired into learning pipeline)
        try:
            from superlocalmemory.core.graph_analyzer import GraphAnalyzer
            conn_ga = sqlite3.connect(self._memory_db, timeout=10)
            conn_ga.execute("PRAGMA busy_timeout=5000")
            conn_ga.row_factory = sqlite3.Row

            class _DBProxy:
                """Minimal DB proxy for GraphAnalyzer compatibility."""
                def __init__(self, connection: sqlite3.Connection) -> None:
                    self._conn = connection
                def execute(self, sql: str, params: tuple = ()) -> list:
                    cursor = self._conn.execute(sql, params)
                    if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE")):
                        self._conn.commit()
                        return []
                    return cursor.fetchall()

            ga = GraphAnalyzer(_DBProxy(conn_ga))
            if not dry_run:
                ga_result = ga.compute_and_store(profile_id)
                stats["graph_nodes"] = ga_result.get("node_count", 0)
                stats["graph_communities"] = ga_result.get("community_count", 0)
                logger.info(
                    "Graph analysis: %d nodes, %d communities",
                    stats["graph_nodes"], stats["graph_communities"],
                )
            conn_ga.close()
        except Exception as exc:
            logger.debug("Graph analysis failed: %s", exc)

        # 5. Check if ranker should retrain
        try:
            from superlocalmemory.learning.feedback import FeedbackCollector
            collector = FeedbackCollector(Path(self._learning_db))
            signal_count = collector.get_feedback_count(profile_id)
            stats["signal_count"] = signal_count
            stats["ranker_phase"] = 1 if signal_count < 50 else (2 if signal_count < 200 else 3)

            # Auto-retrain at threshold crossings
            if signal_count >= 200 and not dry_run:
                retrained = self._retrain_ranker(profile_id, signal_count)
                stats["retrained"] = retrained
        except Exception as exc:
            logger.debug("Retrain check failed: %s", exc)

        # 6. Entity compilation (v3.4.3: compiled truth per entity)
        if not dry_run:
            try:
                from superlocalmemory.learning.entity_compiler import EntityCompiler
                from superlocalmemory.core.config import SLMConfig
                config = SLMConfig.load()
                compiler = EntityCompiler(self._memory_db, config)
                ec_result = compiler.compile_all(profile_id)
                stats["entities_compiled"] = ec_result.get("compiled", 0)
                if ec_result["compiled"] > 0:
                    logger.info("Entity compilation: %d entities compiled",
                                ec_result["compiled"])
            except Exception as exc:
                logger.debug("Entity compilation failed: %s", exc)

        return stats

    def _deduplicate(self, profile_id: str, dry_run: bool) -> int:
        """Find and mark near-duplicate facts.

        v3.4.21 (LLD-12): prefer HNSW ANN + entity-overlap dedup with a
        reversible merge log. On any error (missing schema columns,
        hnswlib unavailable, RAM budget exceeded) fall back to the
        legacy prefix dedup so existing deployments keep working.

        Never DELETEs from atomic_facts — merges flip archive_status
        and write memory_merge_log rows.
        """
        # --- v3.4.21 preferred path: HNSW + memory_merge (LLD-12) ---
        try:
            from superlocalmemory.learning.hnsw_dedup import HnswDeduplicator
            from superlocalmemory.learning.memory_merge import apply_merges

            dedup = HnswDeduplicator(memory_db_path=self._memory_db)
            candidates = dedup.find_merge_candidates(profile_id)
            if not candidates:
                return 0
            if dry_run:
                return len(candidates)
            applied = apply_merges(
                self._memory_db, candidates, profile_id=profile_id,
            )
            return applied
        except sqlite3.OperationalError as exc:
            # Schema probably predates M011 — fall through to legacy path.
            logger.debug("hnsw dedup schema missing, fallback: %s", exc)
        except Exception as exc:
            logger.debug("hnsw dedup unexpected error, fallback: %s", exc)

        # --- Legacy fallback (pre-v3.4.21 behaviour) ---
        try:
            conn = sqlite3.connect(self._memory_db, timeout=10)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                "SELECT fact_id, content FROM atomic_facts "
                "WHERE profile_id = ? ORDER BY created_at",
                (profile_id,),
            ).fetchall()

            seen_prefixes: dict[str, str] = {}
            duplicates = []

            for r in rows:
                d = dict(r)
                prefix = d["content"][:100].strip().lower()
                if prefix in seen_prefixes:
                    duplicates.append(d["fact_id"])
                else:
                    seen_prefixes[prefix] = d["fact_id"]

            if duplicates and not dry_run:
                for fid in duplicates:
                    conn.execute(
                        "UPDATE atomic_facts SET confidence = MAX(0.1, confidence * 0.5) "
                        "WHERE fact_id = ?",
                        (fid,),
                    )
                conn.commit()

            conn.close()
            return len(duplicates)
        except Exception:
            return 0

    def _generate_patterns(self, profile_id: str, dry_run: bool) -> int:
        """Mine behavioral patterns from ALL memory sources.

        v3.4.1: Expanded from 3 to 7 pattern types. No 500-fact cap.
        Analyzes: facts, signals, co-retrieval edges, channel credits,
        entities, sessions, graph communities.
        """
        try:
            from superlocalmemory.learning.behavioral import BehavioralPatternStore
            import re
            from collections import Counter, defaultdict

            conn = sqlite3.connect(self._memory_db, timeout=10)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row

            # v3.4.1: No cap — analyze ALL facts
            facts = conn.execute(
                "SELECT fact_id, content, fact_type, created_at, session_id, "
                "confidence, canonical_entities_json "
                "FROM atomic_facts "
                "WHERE profile_id = ? AND lifecycle = 'active' "
                "ORDER BY created_at DESC",
                (profile_id,),
            ).fetchall()

            if len(facts) < 5:
                conn.close()
                return 0

            store = BehavioralPatternStore(self._learning_db)
            generated = 0

            # ── 1. Tech Preferences (expanded keyword list) ───────────
            tech_keywords = {
                "python": "Python", "javascript": "JavaScript",
                "typescript": "TypeScript", "react": "React",
                "vue": "Vue", "angular": "Angular",
                "postgresql": "PostgreSQL", "mysql": "MySQL",
                "sqlite": "SQLite", "docker": "Docker",
                "kubernetes": "Kubernetes", "aws": "AWS",
                "azure": "Azure", "gcp": "GCP",
                "node": "Node.js", "fastapi": "FastAPI",
                "django": "Django", "flask": "Flask",
                "rust": "Rust", "go": "Go", "java": "Java",
                "git": "Git", "npm": "npm", "pip": "pip",
                "langchain": "LangChain", "ollama": "Ollama",
                "pytorch": "PyTorch", "claude": "Claude",
                "openai": "OpenAI", "anthropic": "Anthropic",
                "redis": "Redis", "mongodb": "MongoDB",
                "graphql": "GraphQL", "nextjs": "Next.js",
                "terraform": "Terraform", "nginx": "Nginx",
                "linux": "Linux", "macos": "macOS",
                "vscode": "VS Code", "neovim": "Neovim",
            }

            tech_counts: Counter = Counter()
            for f in facts:
                content = dict(f)["content"].lower()
                for keyword, label in tech_keywords.items():
                    if keyword in content:
                        tech_counts[label] += 1

            for tech, count in tech_counts.most_common(20):
                if count >= 2 and not dry_run:
                    confidence = min(1.0, count / max(len(facts) * 0.1, 10))
                    store.record_pattern(
                        profile_id=profile_id,
                        pattern_type="tech_preference",
                        data={"topic": tech, "pattern_key": tech,
                              "value": tech, "key": "tech",
                              "evidence": count},
                        success_rate=confidence,
                        confidence=confidence,
                    )
                    generated += 1

            # ── 2. Topic Interests (word frequency) ───────────────────
            stopwords = frozenset({
                "the", "is", "a", "an", "in", "on", "at", "to", "for",
                "of", "and", "or", "not", "with", "that", "this", "was",
                "are", "be", "has", "had", "have", "from", "by", "it",
                "its", "as", "but", "were", "been", "being", "would",
                "could", "should", "will", "may", "might", "can", "do",
                "does", "did", "about", "into", "over", "after", "before",
                "then", "than", "also", "just", "like", "more", "some",
                "only", "other", "such", "each", "every", "both", "most",
            })
            word_counts: Counter = Counter()
            for f in facts:
                words = re.findall(r'\b[a-zA-Z]{4,}\b', dict(f)["content"].lower())
                for w in words:
                    if w not in stopwords:
                        word_counts[w] += 1

            for topic, count in word_counts.most_common(15):
                if count >= 3 and not dry_run:
                    confidence = min(1.0, count / max(len(facts) * 0.05, 15))
                    store.record_pattern(
                        profile_id=profile_id,
                        pattern_type="interest",
                        data={"topic": topic, "pattern_key": topic,
                              "count": count, "evidence": count},
                        success_rate=confidence,
                        confidence=confidence,
                    )
                    generated += 1

            # ── 3. Temporal Activity Patterns ─────────────────────────
            hour_counts: Counter = Counter()
            for f in facts:
                created = dict(f).get("created_at", "")
                try:
                    if "T" in created:
                        hour = int(created.split("T")[1][:2])
                    elif " " in created:
                        hour = int(created.split(" ")[1][:2])
                    else:
                        continue
                    period = ("morning" if 6 <= hour < 12 else
                              "afternoon" if 12 <= hour < 18 else
                              "evening" if 18 <= hour < 22 else "night")
                    hour_counts[period] += 1
                except (ValueError, IndexError):
                    pass

            total_hours = sum(hour_counts.values())
            for period, count in hour_counts.most_common():
                if count >= 2 and total_hours > 0 and not dry_run:
                    pct = round(count / total_hours * 100)
                    store.record_pattern(
                        profile_id=profile_id,
                        pattern_type="temporal",
                        data={"topic": period, "pattern_key": period,
                              "value": f"{period} ({pct}%)",
                              "evidence": count, "key": period,
                              "distribution": dict(hour_counts)},
                        success_rate=pct / 100,
                        confidence=min(1.0, count / max(total_hours * 0.1, 5)),
                    )
                    generated += 1

            # ── 4. Entity Preferences (v3.4.1 NEW) ───────────────────
            import json as _json
            entity_counts: Counter = Counter()
            for f in facts:
                raw = dict(f).get("canonical_entities_json", "")
                if raw:
                    try:
                        for ent in _json.loads(raw):
                            entity_counts[ent] += 1
                    except (ValueError, TypeError):
                        pass

            # v3.4.7: Resolve entity IDs to readable canonical names
            entity_names: dict = {}
            try:
                eid_list = list(entity_counts.keys())
                if eid_list:
                    placeholders = ",".join("?" * len(eid_list))
                    name_rows = conn.execute(
                        f"SELECT entity_id, canonical_name FROM canonical_entities "
                        f"WHERE entity_id IN ({placeholders})",
                        eid_list,
                    ).fetchall()
                    entity_names = {dict(r)["entity_id"]: dict(r)["canonical_name"] for r in name_rows}
            except Exception:
                pass

            for entity, count in entity_counts.most_common(15):
                if count >= 3 and not dry_run:
                    readable = entity_names.get(entity, entity)
                    confidence = min(1.0, count / max(len(facts) * 0.05, 10))
                    store.record_pattern(
                        profile_id=profile_id,
                        pattern_type="entity_preferences",
                        data={"topic": readable, "pattern_key": f"entity:{readable}",
                              "value": readable, "evidence": count,
                              "source": "entity_frequency"},
                        success_rate=confidence,
                        confidence=confidence,
                    )
                    generated += 1

            # ── 5. Session Activity Patterns (v3.4.1 NEW) ────────────
            session_counts: Counter = Counter()
            for f in facts:
                sid = dict(f).get("session_id", "")
                if sid:
                    session_counts[sid] += 1

            if session_counts:
                avg_facts_per_session = sum(session_counts.values()) / len(session_counts)
                heavy_sessions = [s for s, c in session_counts.items() if c > avg_facts_per_session * 2]
                if heavy_sessions and not dry_run:
                    store.record_pattern(
                        profile_id=profile_id,
                        pattern_type="session_activity",
                        data={"pattern_key": "heavy_session_usage",
                              "value": f"{len(heavy_sessions)} intensive sessions",
                              "evidence": len(heavy_sessions),
                              "avg_facts": round(avg_facts_per_session, 1),
                              "total_sessions": len(session_counts)},
                        success_rate=0.8,
                        confidence=min(1.0, len(heavy_sessions) / 5),
                    )
                    generated += 1

            # ── 6. Fact Type Distribution (v3.4.1 NEW) ────────────────
            type_counts: Counter = Counter()
            for f in facts:
                ft = dict(f).get("fact_type", "semantic")
                type_counts[ft] += 1

            total_ft = sum(type_counts.values())
            if total_ft > 0 and not dry_run:
                dominant_type = type_counts.most_common(1)[0]
                pct = round(dominant_type[1] / total_ft * 100)
                store.record_pattern(
                    profile_id=profile_id,
                    pattern_type="fact_type_distribution",
                    data={"pattern_key": "memory_style",
                          "value": f"{dominant_type[0]} dominant ({pct}%)",
                          "evidence": dominant_type[1],
                          "distribution": dict(type_counts)},
                    success_rate=pct / 100,
                    confidence=min(1.0, dominant_type[1] / 20),
                )
                generated += 1

            # ── 7. Channel Performance (v3.4.1 NEW — from signals) ────
            try:
                learn_conn = sqlite3.connect(self._learning_db, timeout=10)
                learn_conn.row_factory = sqlite3.Row

                # Retrieval usage patterns from learning_feedback
                channel_rows = learn_conn.execute(
                    "SELECT channel, COUNT(*) AS cnt, "
                    "AVG(signal_value) AS avg_signal "
                    "FROM learning_feedback "
                    "WHERE profile_id = ? "
                    "GROUP BY channel ORDER BY cnt DESC",
                    (profile_id,),
                ).fetchall()

                for row in channel_rows:
                    d = dict(row)
                    ch = d.get("channel", "unknown")
                    cnt = d.get("cnt", 0)
                    avg_sig = round(float(d.get("avg_signal", 0) or 0), 3)
                    if cnt >= 5 and not dry_run:
                        store.record_pattern(
                            profile_id=profile_id,
                            pattern_type="channel_performance",
                            data={"pattern_key": f"channel:{ch}",
                                  "value": f"{ch} ({cnt} hits, {avg_sig} avg)",
                                  "evidence": cnt,
                                  "avg_signal": avg_sig},
                            success_rate=avg_sig,
                            confidence=min(1.0, cnt / 50),
                        )
                        generated += 1

                # Co-retrieval cluster patterns
                try:
                    coret_rows = learn_conn.execute(
                        "SELECT fact_a, fact_b, co_access_count "
                        "FROM co_retrieval_edges "
                        "WHERE profile_id = ? AND co_access_count >= 3 "
                        "ORDER BY co_access_count DESC LIMIT 20",
                        (profile_id,),
                    ).fetchall()
                    if coret_rows and not dry_run:
                        store.record_pattern(
                            profile_id=profile_id,
                            pattern_type="co_retrieval_clusters",
                            data={"pattern_key": "co_retrieval_clusters",
                                  "value": f"{len(coret_rows)} strong fact pairs",
                                  "evidence": len(coret_rows),
                                  "top_pair_count": dict(coret_rows[0]).get("co_access_count", 0) if coret_rows else 0},
                            success_rate=0.7,
                            confidence=min(1.0, len(coret_rows) / 10),
                        )
                        generated += 1
                except Exception:
                    pass

                learn_conn.close()
            except Exception as exc:
                logger.debug("Signal pattern mining failed: %s", exc)

            # ── 8. Community Membership (v3.4.1 NEW — from graph) ─────
            try:
                comm_rows = conn.execute(
                    "SELECT community_id, COUNT(*) AS cnt "
                    "FROM fact_importance "
                    "WHERE profile_id = ? AND community_id IS NOT NULL "
                    "GROUP BY community_id ORDER BY cnt DESC",
                    (profile_id,),
                ).fetchall()
                if comm_rows and not dry_run:
                    total_comm = sum(dict(r)["cnt"] for r in comm_rows)
                    store.record_pattern(
                        profile_id=profile_id,
                        pattern_type="knowledge_structure",
                        data={"pattern_key": "knowledge_structure",
                              "value": f"{len(comm_rows)} topic communities, {total_comm} classified facts",
                              "evidence": total_comm,
                              "community_count": len(comm_rows)},
                        success_rate=0.8,
                        confidence=min(1.0, len(comm_rows) / 5),
                    )
                    generated += 1
            except Exception:
                pass

            conn.close()

            logger.info(
                "Pattern mining: %d patterns generated for profile %s "
                "from %d facts",
                generated, profile_id, len(facts),
            )
            return generated
        except Exception as exc:
            logger.warning("Pattern generation error: %s", exc)
            return 0

    def _retrain_ranker(self, profile_id: str, signal_count: int) -> bool:
        """Retrain the adaptive ranker (LLD-02 §4.6).

        Uses real 20-dim feature vectors + integer labels + group param,
        trained with ``objective="lambdarank"``. Persisted via
        ``LearningDatabase.persist_model`` with SHA-256 integrity.
        """
        try:
            return _retrain_ranker_impl(self._learning_db, profile_id)
        except Exception as exc:
            logger.debug("Retrain failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# LLD-02 §4.6 — lambdarank retraining
# ---------------------------------------------------------------------------


def _retrain_ranker_impl(
    learning_db: str | Path,
    profile_id: str,
    *,
    include_synthetic: bool = False,
) -> bool:
    """Real training path — pure function so tests can call it directly.

    ``include_synthetic`` forwards to
    :meth:`LearningDatabase.fetch_training_examples` so migrated legacy
    rows (``is_synthetic=1``) participate in training when the user opts
    in via the dashboard "Migrate legacy data" flow.
    """
    try:
        import numpy as np
        import lightgbm as lgb  # noqa: PLC0415
    except ImportError:
        logger.info("lightgbm or numpy missing; skipping retrain")
        return False

    from superlocalmemory.learning.database import LearningDatabase
    from superlocalmemory.learning.features import FEATURE_NAMES
    from superlocalmemory.learning.labeler import label_for_row, label_gain

    db = LearningDatabase(learning_db)
    rows = db.fetch_training_examples(
        profile_id=profile_id,
        limit=2000,
        min_outcome_age_sec=60,
        include_synthetic=include_synthetic,
    )
    if len(rows) < 200:
        logger.info(
            "retrain: need ≥200 rows, have %d — deferring", len(rows),
        )
        return False

    X, y_int, groups = _build_training_matrix(rows, FEATURE_NAMES)
    if groups is None or len(groups) < 2:
        logger.info("retrain: insufficient query groups (%s) — deferring",
                    None if groups is None else len(groups))
        return False
    assert sum(groups) == X.shape[0], (
        f"group sum mismatch: {sum(groups)} != {X.shape[0]}")

    gain = label_gain()
    # Defensive: clamp any out-of-range label.
    y_int = np.clip(y_int, 0, len(gain) - 1)

    ds_train = lgb.Dataset(
        X,
        label=y_int,
        group=groups,
        feature_name=list(FEATURE_NAMES),
        free_raw_data=False,
    )
    # MKT-v2-M-01: allow switching between ``lambdarank`` (default, LLD-02
    # CR1) and ``rank_xendcg`` (verified as faster with comparable NDCG per
    # verification-2026-04-17.md claim 6). Operators can A/B on real data
    # by flipping ``SLM_LGBM_OBJECTIVE`` without touching code.
    _allowed_objectives = {"lambdarank", "rank_xendcg"}
    objective = os.environ.get("SLM_LGBM_OBJECTIVE", "lambdarank").strip()
    if objective not in _allowed_objectives:
        logger.warning(
            "SLM_LGBM_OBJECTIVE=%r not in %s; defaulting to lambdarank",
            objective, sorted(_allowed_objectives),
        )
        objective = "lambdarank"
    params = {
        "objective": objective,
        "metric": "ndcg",
        "ndcg_eval_at": [1, 3, 5, 10],
        "label_gain": gain,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 20,
        "verbosity": -1,
        "num_threads": max(1, (os.cpu_count() or 2) - 1),
    }
    try:
        booster_new = lgb.train(params, ds_train, num_boost_round=50)
    except lgb.basic.LightGBMError as exc:
        logger.warning("retrain: lightgbm train failed: %s", exc)
        return False

    # Shadow test: only promote if better than prior active model.
    prior = db.load_active_model(profile_id)
    if prior is not None:
        if not _shadow_test_improved(prior, booster_new, rows, FEATURE_NAMES):
            logger.info("Shadow test: new model did not beat prior; keeping")
            return False

    model_str = booster_new.model_to_string()
    state_bytes = model_str.encode("utf-8")
    sha = hashlib.sha256(state_bytes).hexdigest()
    try:
        db.persist_model(
            profile_id=profile_id,
            state_bytes=state_bytes,
            bytes_sha256=sha,
            feature_names=list(FEATURE_NAMES),
            trained_on_count=len(rows),
            metrics=_compute_eval_metrics(booster_new, rows, FEATURE_NAMES),
        )
    except Exception as exc:
        logger.warning("persist_model failed: %s", exc)
        return False

    # Invalidate in-process cache so new model is picked up.
    try:
        from superlocalmemory.learning.model_cache import invalidate
        invalidate(profile_id)
    except Exception:  # pragma: no cover — defensive
        pass

    logger.info(
        "Ranker retrained (lambdarank): %d rows, %d groups, promoted=True",
        len(rows), len(groups),
    )
    return True


def _build_training_matrix(rows: list[dict], feature_names):
    """Group rows by ``query_id``, preserve order by ``position``.

    Returns (X, y_int, group_counts).  ``group_counts`` is None when no
    groups are discoverable.
    """
    import numpy as np
    from superlocalmemory.learning.labeler import label_for_row

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        qid = row.get("query_id") or ""
        grouped.setdefault(qid, []).append(row)
    if not grouped:
        return np.zeros((0, len(feature_names)), dtype=np.float32), [], None

    xs: list[list[float]] = []
    ys: list[int] = []
    group_counts: list[int] = []
    for qid, group_rows in grouped.items():
        # Sort by position ascending; missing positions land at the end.
        group_rows = sorted(
            group_rows,
            key=lambda r: (r.get("position") if r.get("position") is not None
                           else 10**9),
        )
        for r in group_rows:
            feats = r.get("features") or {}
            xs.append([float(feats.get(n, 0.0)) for n in feature_names])
            ys.append(label_for_row(r))
        group_counts.append(len(group_rows))

    X = np.asarray(xs, dtype=np.float32)
    y = np.asarray(ys, dtype=np.int32)
    return X, y, group_counts


def _shadow_test_improved(prior_row, booster_new, rows, feature_names) -> bool:
    """Return True iff new booster beats prior on NDCG@10 with p<0.05.

    Lightweight paired t-test across per-query NDCG@10 scores.
    ``prior_row`` is the dict returned by ``load_active_model`` — may be
    unusable (no state_bytes / unparseable); in that case we promote.
    """
    try:
        import numpy as np
        import lightgbm as lgb
    except ImportError:  # pragma: no cover
        return True

    try:
        prior_booster = lgb.Booster(
            model_str=bytes(prior_row["state_bytes"]).decode("utf-8"),
        )
    except Exception:
        return True  # prior unusable → promote new.

    X, y, groups = _build_training_matrix(rows, feature_names)
    if groups is None or not groups:
        return True

    offsets = [0]
    for g in groups:
        offsets.append(offsets[-1] + g)

    def _ndcg_at_k(scores, labels, k=10):
        order = np.argsort(-scores)
        gains_map = [0, 1, 3, 7, 15]
        dcg = 0.0
        for i, idx in enumerate(order[:k]):
            l = int(labels[idx])
            if 0 <= l < len(gains_map):
                dcg += gains_map[l] / np.log2(i + 2)
        ideal = sorted(labels.tolist(), reverse=True)[:k]
        idcg = sum(
            (gains_map[int(l)] if 0 <= int(l) < len(gains_map) else 0)
            / np.log2(i + 2)
            for i, l in enumerate(ideal)
        )
        return dcg / idcg if idcg > 0 else 0.0

    old_ndcgs: list[float] = []
    new_ndcgs: list[float] = []
    for i in range(len(groups)):
        lo, hi = offsets[i], offsets[i + 1]
        if hi - lo < 2:
            continue
        Xg, yg = X[lo:hi], y[lo:hi]
        try:
            s_old = prior_booster.predict(Xg)
            s_new = booster_new.predict(Xg)
        except Exception:
            return False
        old_ndcgs.append(_ndcg_at_k(s_old, yg))
        new_ndcgs.append(_ndcg_at_k(s_new, yg))

    if not old_ndcgs:
        return True
    old_arr = np.asarray(old_ndcgs)
    new_arr = np.asarray(new_ndcgs)
    delta = float(np.mean(new_arr - old_arr))
    if delta < 0.02:
        return False

    # Paired t-test — small-sample safe.
    diff = new_arr - old_arr
    n = len(diff)
    if n < 2:
        return True
    mean = float(np.mean(diff))
    std = float(np.std(diff, ddof=1))
    if std == 0.0:
        return mean > 0
    t_stat = mean / (std / np.sqrt(n))
    # Rough threshold: t > 2.0 (~p<0.05 for n ≥ 10 two-tailed).
    return t_stat > 2.0


def _compute_eval_metrics(booster, rows, feature_names) -> dict:
    """Lightweight training metrics snapshot."""
    try:
        import numpy as np
        X, y, groups = _build_training_matrix(rows, feature_names)
        preds = booster.predict(X) if X.size else np.zeros(0)
        return {
            "n_rows": int(X.shape[0]),
            "n_groups": int(len(groups or [])),
            "mean_score": float(np.mean(preds)) if preds.size else 0.0,
        }
    except Exception:  # pragma: no cover
        return {}
