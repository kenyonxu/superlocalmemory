# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory v3.4.21 — LLD-12 (Real Consolidation)

"""HNSW-backed near-duplicate detection + reward-gated Ebbinghaus archive.

LLD-12 drives three related flows:

1. ``HnswDeduplicator.find_merge_candidates`` — ANN neighbour search
   over embedded ``atomic_facts`` rows. Honours LLD-12 §2 thresholds
   (cosine > 0.95 AND entity_jaccard > 0.8). Uses ``ram_reservation``
   (LLD-00 §7) before the build; frees the index after. Falls back to
   prefix-dedup when hnswlib is unavailable, when RAM budget is
   exceeded, or when the fact count exceeds ``MAX_FACTS_FOR_HNSW``.

2. ``run_reward_gated_archive`` — flags Ebbinghaus-cold facts as
   archived *only* when they show no positive reward in the last 60
   days AND are not marked important (LLD-12 §4). Writes a
   payload-preserving row to ``memory_archive`` and updates
   ``atomic_facts.archive_status='archived'``. **Never issues
   DELETE FROM atomic_facts** (SOUL directive, LLD-12 §1).

3. ``apply_strong_memory_boost`` — nudges ``atomic_facts.retrieval_prior``
   upward for facts with recurring high reward, capped at 0.5 (LLD-12 §5).

All writes flow through the atomic UPDATE path in ``memory_merge.py``;
this module only coordinates selection + scoring.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from superlocalmemory.core.ram_lock import ram_reservation

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_embedding(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        vec = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(vec, list) or not vec:
        return None
    try:
        return [float(x) for x in vec]
    except (TypeError, ValueError):
        return None


def _cosine(u: Sequence[float], v: Sequence[float]) -> float:
    dot = 0.0
    nu = 0.0
    nv = 0.0
    for a, b in zip(u, v):
        dot += a * b
        nu += a * a
        nv += b * b
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return dot / (math.sqrt(nu) * math.sqrt(nv))


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


class HnswDeduplicator:
    """Find near-duplicate ``atomic_facts`` rows via HNSW ANN + entity overlap.

    Contract (LLD-12 §2.1):
      - cosine > COSINE_THRESHOLD AND jaccard > ENTITY_JACCARD_THRESHOLD
      - Canonical = higher importance, tie-break older created_at
      - Never delete; merges happen through memory_merge.apply_merges
    """

    COSINE_THRESHOLD: float = 0.95
    ENTITY_JACCARD_THRESHOLD: float = 0.8
    MAX_FACTS_FOR_HNSW: int = 200_000

    # Per-vector HNSW footprint estimate (LLD-12 §3.1).
    _BYTES_PER_VEC_DEFAULT: int = 384 * 4 + 16 * 8 * 2

    def __init__(self, *, memory_db_path: str | Path) -> None:
        self._db = Path(memory_db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_merge_candidates(
        self,
        profile_id: str,
        *,
        wall_seconds: float = 300.0,
        _force_unavailable: bool = False,
    ) -> list[tuple[str, str, float, float]]:
        """Return ``(canonical_id, duplicate_id, cosine, jaccard)`` tuples.

        Never raises for expected failure modes — falls back to prefix
        dedup instead. ``wall_seconds`` is the soft budget; we stop
        emitting new candidates once exceeded.
        """
        deadline = time.monotonic() + max(0.0, wall_seconds)

        rows = self._fetch_live_facts(profile_id)
        if len(rows) < 2:
            return []
        if len(rows) > self.MAX_FACTS_FOR_HNSW:
            logger.info(
                "hnsw_dedup: %d facts > MAX %d → prefix fallback",
                len(rows), self.MAX_FACTS_FOR_HNSW,
            )
            return self._prefix_fallback(rows, deadline)

        # Estimate RAM; let the reservation reject if the system is tight.
        est_mb = self._estimate_ram_mb(len(rows), dim=self._detect_dim(rows))
        required_mb = max(16, int(est_mb * 1.2))

        hnswlib_mod = None
        if not _force_unavailable:
            try:
                import hnswlib as hnswlib_mod  # type: ignore  # noqa: PLC0415
            except ImportError:
                hnswlib_mod = None

        if hnswlib_mod is None:
            logger.info("hnsw_dedup: hnswlib unavailable → prefix fallback")
            return self._prefix_fallback(rows, deadline)

        try:
            with ram_reservation(
                "hnswlib",
                required_mb=required_mb,
                timeout_s=min(30.0, max(1.0, wall_seconds)),
            ):
                return self._ann_candidates(rows, hnswlib_mod, deadline)
        except RuntimeError as exc:
            logger.info("hnsw_dedup: ram_reservation refused → fallback (%s)", exc)
            return self._prefix_fallback(rows, deadline)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_live_facts(self, profile_id: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(str(self._db), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT fact_id, content, canonical_entities_json, "
                "       embedding, importance, confidence, created_at "
                "FROM atomic_facts "
                "WHERE profile_id = ? "
                "  AND (archive_status IS NULL OR archive_status = 'live') "
                "  AND (importance IS NULL OR importance < 1.0) "
                "ORDER BY created_at ASC",
                (profile_id,),
            )
            rows: list[dict[str, Any]] = []
            for r in cursor.fetchall():
                rows.append({
                    "fact_id": r["fact_id"],
                    "content": r["content"] or "",
                    "entities": json.loads(r["canonical_entities_json"] or "[]"),
                    "embedding": _parse_embedding(r["embedding"]),
                    "importance": float(r["importance"] or 0.0),
                    "confidence": float(r["confidence"] or 0.0),
                    "created_at": r["created_at"] or "",
                })
            return rows
        finally:
            conn.close()

    @staticmethod
    def _detect_dim(rows: list[dict[str, Any]]) -> int:
        for r in rows:
            emb = r.get("embedding")
            if emb:
                return len(emb)
        return 384

    def _estimate_ram_mb(self, n: int, *, dim: int) -> float:
        bytes_per_vec = dim * 4 + 16 * 8 * 2
        return (n * bytes_per_vec * 1.10) / (1024 * 1024)

    def _ann_candidates(
        self,
        rows: list[dict[str, Any]],
        hnswlib_mod,
        deadline: float,
    ) -> list[tuple[str, str, float, float]]:
        embedded = [r for r in rows if r["embedding"] is not None]
        if len(embedded) < 2:
            return self._prefix_fallback(rows, deadline)

        dim = len(embedded[0]["embedding"])
        # Align: drop rows with mismatched dim.
        embedded = [r for r in embedded if len(r["embedding"]) == dim]
        if len(embedded) < 2:
            return self._prefix_fallback(rows, deadline)

        index = hnswlib_mod.Index(space="cosine", dim=dim)
        index.init_index(max_elements=len(embedded), ef_construction=100, M=16)
        index.set_ef(min(50, len(embedded)))

        try:
            for i, r in enumerate(embedded):
                index.add_items([r["embedding"]], [i])

            k = min(6, len(embedded))
            candidates: list[tuple[str, str, float, float]] = []
            seen_losers: set[str] = set()

            for i, r in enumerate(embedded):
                if time.monotonic() > deadline:
                    break
                labels, distances = index.knn_query(
                    [r["embedding"]], k=k,
                )
                lbls = labels[0] if hasattr(labels, "__iter__") else labels
                dsts = distances[0] if hasattr(distances, "__iter__") else distances
                for nb_idx, dist in zip(lbls, dsts):
                    if int(nb_idx) == i:
                        continue
                    neighbour = embedded[int(nb_idx)]
                    if neighbour["fact_id"] in seen_losers:
                        continue
                    if r["fact_id"] in seen_losers:
                        break
                    # hnswlib cosine distance is (1 - cos).
                    cos = max(0.0, min(1.0, 1.0 - float(dist)))
                    if cos <= self.COSINE_THRESHOLD:
                        continue
                    jac = _jaccard(r["entities"], neighbour["entities"])
                    if jac <= self.ENTITY_JACCARD_THRESHOLD:
                        continue
                    canonical, loser = _pick_canonical(r, neighbour)
                    if loser["fact_id"] in seen_losers:
                        continue
                    candidates.append(
                        (canonical["fact_id"], loser["fact_id"], cos, jac),
                    )
                    seen_losers.add(loser["fact_id"])
            return candidates
        finally:
            # Free ANN RAM immediately (LLD-12 §3.3).
            del index

    def _prefix_fallback(
        self,
        rows: list[dict[str, Any]],
        deadline: float,
    ) -> list[tuple[str, str, float, float]]:
        """Content-prefix dedup — retained behaviour when hnswlib cannot run."""
        seen_prefix: dict[str, dict[str, Any]] = {}
        candidates: list[tuple[str, str, float, float]] = []
        for r in rows:
            if time.monotonic() > deadline:
                break
            prefix = (r["content"] or "")[:100].strip().lower()
            if not prefix:
                continue
            prior = seen_prefix.get(prefix)
            if prior is None:
                seen_prefix[prefix] = r
                continue
            canonical, loser = _pick_canonical(prior, r)
            jac = _jaccard(prior["entities"], r["entities"])
            candidates.append(
                (canonical["fact_id"], loser["fact_id"], 1.0, jac),
            )
        return candidates


def _pick_canonical(
    a: dict[str, Any], b: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Canonical = higher importance, tie-break: higher confidence, older."""
    ai, bi = float(a.get("importance", 0.0)), float(b.get("importance", 0.0))
    if ai != bi:
        return (a, b) if ai > bi else (b, a)
    ac, bc = float(a.get("confidence", 0.0)), float(b.get("confidence", 0.0))
    if ac != bc:
        return (a, b) if ac > bc else (b, a)
    at, bt = a.get("created_at", ""), b.get("created_at", "")
    return (a, b) if at <= bt else (b, a)


# ---------------------------------------------------------------------------
# Reward-gated Ebbinghaus archive
# ---------------------------------------------------------------------------

REWARD_WINDOW_DAYS: int = 60
ARCHIVE_REWARD_THRESHOLD: float = 0.3


def run_reward_gated_archive(
    memory_db_path: str | Path,
    profile_id: str,
    *,
    candidate_fact_ids: list[str],
) -> list[str]:
    """Archive candidate facts that have no positive reward in 60 days and
    are not flagged important. Returns the list of fact_ids archived.

    LLD-12 §1 hard invariant: this function NEVER issues
    ``DELETE FROM atomic_facts``. It UPDATEs archive_status + writes a
    payload snapshot to ``memory_archive``.
    """
    if not candidate_fact_ids:
        return []

    archived: list[str] = []
    conn = sqlite3.connect(str(memory_db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=2000")

    try:
        placeholders = ",".join("?" for _ in candidate_fact_ids)
        rows = conn.execute(
            f"SELECT fact_id, content, canonical_entities_json, importance, "
            f"       confidence, embedding, created_at "
            f"FROM atomic_facts "
            f"WHERE profile_id = ? AND fact_id IN ({placeholders}) "
            f"  AND (archive_status IS NULL OR archive_status = 'live')",
            (profile_id, *candidate_fact_ids),
        ).fetchall()

        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            fid = row["fact_id"]
            # 1. Important flag skip (LLD-12 §4 criterion 3).
            if float(row["importance"] or 0.0) >= 1.0:
                continue

            # 2. Recent positive reward skip (criterion 2).
            cutoff_sql = (
                f"datetime('now', '-{REWARD_WINDOW_DAYS} days')"
            )
            recent = conn.execute(
                "SELECT 1 FROM action_outcomes "
                "WHERE profile_id = ? "
                "  AND reward IS NOT NULL AND reward > ? "
                "  AND fact_ids_json LIKE ? "
                f"  AND COALESCE(settled_at, '') >= {cutoff_sql} "
                "LIMIT 1",
                (profile_id, ARCHIVE_REWARD_THRESHOLD, f'%"{fid}"%'),
            ).fetchone()
            if recent is not None:
                continue

            payload = {
                "fact_id": fid,
                "content": row["content"],
                "canonical_entities_json": row["canonical_entities_json"],
                "importance": row["importance"],
                "confidence": row["confidence"],
                "embedding": row["embedding"],
                "created_at": row["created_at"],
            }
            conn.execute(
                "INSERT INTO memory_archive "
                "(archive_id, fact_id, profile_id, payload_json, "
                " archived_at, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    fid,
                    profile_id,
                    json.dumps(payload),
                    _iso_now(),
                    "reward_gated_ebbinghaus",
                ),
            )
            conn.execute(
                "UPDATE atomic_facts "
                "SET archive_status='archived', "
                "    archive_reason='reward_gated_ebbinghaus' "
                "WHERE fact_id=? AND (archive_status IS NULL OR archive_status='live')",
                (fid,),
            )
            archived.append(fid)

        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("run_reward_gated_archive rollback: %s", exc)
    finally:
        conn.close()

    return archived


# ---------------------------------------------------------------------------
# Strong-memory boost
# ---------------------------------------------------------------------------

STRONG_BOOST_INCREMENT: float = 0.1
STRONG_BOOST_CAP: float = 0.5
STRONG_BOOST_MIN_OUTCOMES: int = 3
STRONG_BOOST_MIN_MEAN: float = 0.7


def apply_strong_memory_boost(
    memory_db_path: str | Path, profile_id: str,
) -> int:
    """Nudge retrieval_prior up for high-reward facts, capped at 0.5.

    Eligibility: ≥ MIN_OUTCOMES outcomes with mean reward > MIN_MEAN.
    Effect: retrieval_prior = MIN(retrieval_prior + INCREMENT, CAP).

    Returns number of rows boosted.
    """
    conn = sqlite3.connect(str(memory_db_path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=2000")
    boosted = 0
    try:
        # Aggregate outcomes per fact_id. fact_ids_json is a JSON array;
        # facts with one element dominate — grab those via LIKE for now.
        # (Full JSON1 parsing is a future optimisation.)
        rows = conn.execute(
            "SELECT fact_id FROM atomic_facts WHERE profile_id=? "
            "  AND (archive_status IS NULL OR archive_status='live')",
            (profile_id,),
        ).fetchall()
        if not rows:
            return 0

        conn.execute("BEGIN IMMEDIATE")
        for (fid,) in rows:
            agg = conn.execute(
                "SELECT COUNT(*) AS c, AVG(reward) AS m "
                "FROM action_outcomes "
                "WHERE profile_id=? AND reward IS NOT NULL "
                "  AND fact_ids_json LIKE ?",
                (profile_id, f'%"{fid}"%'),
            ).fetchone()
            if agg is None:
                continue
            count, mean = agg
            if (count or 0) < STRONG_BOOST_MIN_OUTCOMES:
                continue
            if (mean or 0.0) <= STRONG_BOOST_MIN_MEAN:
                continue
            conn.execute(
                "UPDATE atomic_facts "
                "SET retrieval_prior = MIN(COALESCE(retrieval_prior, 0) + ?, ?) "
                "WHERE fact_id=?",
                (STRONG_BOOST_INCREMENT, STRONG_BOOST_CAP, fid),
            )
            boosted += 1
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("apply_strong_memory_boost rollback: %s", exc)
    finally:
        conn.close()
    return boosted


# ---------------------------------------------------------------------------
# Reward-aware soft-prompt candidate selection
# ---------------------------------------------------------------------------


def select_high_reward_fact_ids(
    memory_db_path: str | Path,
    profile_id: str,
    *,
    min_reward: float = 0.6,
    min_outcomes: int = 1,
) -> list[str]:
    """Return fact_ids whose mean outcome reward ≥ ``min_reward``.

    Used by ``soft_prompt_generator`` to mine only high-reward facts
    (LLD-12 §6).
    """
    conn = sqlite3.connect(str(memory_db_path), timeout=10.0)
    try:
        fact_rows = conn.execute(
            "SELECT fact_id FROM atomic_facts WHERE profile_id=? "
            "  AND (archive_status IS NULL OR archive_status='live')",
            (profile_id,),
        ).fetchall()
        out: list[str] = []
        for (fid,) in fact_rows:
            agg = conn.execute(
                "SELECT COUNT(*), AVG(reward) FROM action_outcomes "
                "WHERE profile_id=? AND reward IS NOT NULL "
                "  AND fact_ids_json LIKE ?",
                (profile_id, f'%"{fid}"%'),
            ).fetchone()
            if agg is None:
                continue
            count, mean = agg
            if (count or 0) < min_outcomes:
                continue
            if (mean or 0.0) >= min_reward:
                out.append(fid)
        return out
    finally:
        conn.close()


__all__ = (
    "HnswDeduplicator",
    "run_reward_gated_archive",
    "apply_strong_memory_boost",
    "select_high_reward_fact_ids",
    "REWARD_WINDOW_DAYS",
    "ARCHIVE_REWARD_THRESHOLD",
    "STRONG_BOOST_INCREMENT",
    "STRONG_BOOST_CAP",
)
