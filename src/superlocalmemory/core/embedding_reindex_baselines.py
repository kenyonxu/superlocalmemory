# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Measure the staged space's hub baselines before the swap.

Recall subtracts a memory's hub baseline (its typical cosine to the fixed probe
queries, ``retrieval/hub_baseline.py``) from its cosine to the question. The
rows are per embedding space. A switch used to publish a new space with none
(0 of 27,236 rows on the author's store), so until the maintenance backfill
caught up every memory was judged without one.

This pass reads the staged vectors (``embedding_next``), measures them against
the probes embedded with the TARGET model, and stages one row per memory in
``reembed_next_hub``; ``embedding_space_swap.activate`` publishes them in the
swap's own transaction. It runs with no lock held, in short write batches that
rest as long as they took (like staging), and a memory re-staged since is
measured again because staging drops its row. Anything it could not measure is
left for the backfill: a missing baseline is slow to fill, never wrong.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import numpy as np

from superlocalmemory.core import embedding_reindex_steps as steps
from superlocalmemory.retrieval import hub_baseline
from superlocalmemory.storage import embedding_canonical_slots as slots
from superlocalmemory.storage import embedding_spaces as sp

logger = logging.getLogger(__name__)

BATCH = 512


def _pending(conn: Any, after: int) -> list[tuple[int, str, bytes]]:
    twin = slots.next_name("embedding")
    return [(int(r[0]), str(r[1]), r[2]) for r in conn.execute(
        f"SELECT f.rowid, f.fact_id, f.{twin} FROM atomic_facts f WHERE f.rowid > ? "
        f"AND f.{twin} IS NOT NULL AND NOT EXISTS "
        f"(SELECT 1 FROM {sp.NEXT_HUB} h WHERE h.fact_id = f.fact_id) "
        "ORDER BY f.rowid LIMIT ?", (after, BATCH))]


def _measure(rows: list[tuple[int, str, bytes]], probes: np.ndarray) -> list[tuple]:
    width = probes.shape[1]
    usable = [(fid, np.frombuffer(blob, dtype=np.float32)) for _r, fid, blob in rows]
    usable = [(fid, vec) for fid, vec in usable if vec.shape[0] == width]
    if not usable:
        return []
    values = hub_baseline.baselines([v for _f, v in usable], probes)
    return [(fid, hub_baseline.PROBE_SET_VERSION, mean, topk)
            for (fid, _v), (mean, topk) in zip(usable, values)]


def stage_baselines(conn: Any, db_path: Any, probes: np.ndarray | None,
                    check: Callable[[], None]) -> int:
    """Stage a baseline for every staged vector that lacks one; rows written.

    ``probes`` None (the target model could not embed them) stages nothing.
    ``check`` raises when the job is cancelled or the daemon is stopping.
    """
    if probes is None or slots.missing_next(conn) < 0:  # no probes, or no staged twin column
        return 0
    with steps.write_txn(conn, db_path):
        sp.ensure_side_tables(conn)  # a job begun before this table existed
    written, after = 0, 0
    while True:
        check()
        steps._yield_to_recall()
        started = time.perf_counter()
        rows = _pending(conn, after)
        if not rows:
            return written
        measured = _measure(rows, probes)
        with steps.write_txn(conn, db_path):
            conn.executemany(
                f"INSERT OR REPLACE INTO {sp.NEXT_HUB} (fact_id, probe_version, "
                "baseline_mean, baseline_topk) VALUES (?, ?, ?, ?)", measured)
        written += len(measured)
        after = rows[-1][0]
        rest = max(steps.PAUSE_S, (time.perf_counter() - started) * steps.DUTY_REST)
        if rest:
            time.sleep(rest)


__all__ = ["BATCH", "stage_baselines"]
