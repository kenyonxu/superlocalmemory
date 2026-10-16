# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Hub baselines: how close a memory sits to EVERY query, measured once.

WHY
---
Some memories are close to everything in the embedding space. Receipts, logs
and session stubs made mostly of hex ids, UUIDs and app ids are the usual ones:
on a real store they reach raw cosine 0.73-0.78 against random letters, above
the 0.60 evidence floor, so a nonsense question "finds" them. Their cosine to a
query says little, because their cosine to ANY query is high.

The documented fix for such "hubs" is to judge a similarity against the item's
own typical similarity (CSLS, cross-domain similarity local scaling). Here each
memory's baseline is its cosine against a fixed, versioned set of probe queries
-- deterministic nonsense, id-like strings and neutral questions -- embedded
once per embedding space. Recall can then read ``cosine - baseline``: a hub's
high cosine to gibberish is its baseline, not evidence.

Two baselines are stored per memory: the mean over all probes, and the mean of
its ``TOP_K`` closest probes (the CSLS neighbourhood form). Which one, if any,
earns a vote is decided by measurement (scripts/quality/answerability_eval.py),
not here.

WHEN IT IS COMPUTED
-------------------
* A new memory gets its baseline when its materialization completes
  (``core/engine_ingestion.py``), from probe vectors embedded once per process.
* Existing stores fill in through :class:`Backfill`, run by the maintenance
  scheduler a few minutes after start and then every cycle: bounded batches,
  resumable by construction (each batch takes the facts that still lack a
  baseline for the live space and probe version), stopped with the scheduler.
  Recall works throughout; a memory without a baseline simply has none.
* A different embedding space or probe version is a different baseline; old
  rows are ignored, then replaced as the pass reaches them.

Rows hold fact ids and numbers, never memory text.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Sequence
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

TABLE = "fact_hub_baseline"
PROBE_SET_VERSION = "hub-probes-v1"
TOP_K = 16
BATCH = 256
#: Rows of ``probe_texts()`` that are ordinary words with no meaning ("crimson
#: lantern whistles the pebble"). A question's mean cosine to them is how much
#: it reads like that (``query_profile``); fixed by the probe-set version.
NONSENSE_SENTENCES = slice(32, 64)

_SYLLABLES = ("ka", "lo", "mi", "nu", "pe", "ra", "si", "to", "vu", "ze", "bri",
              "dro", "fla", "gri", "kle", "mor", "pli", "sna", "tro", "wex")
_NOUNS = ("lantern", "harbour", "pebble", "meadow", "kettle", "compass", "ribbon",
          "glacier", "orchard", "saddle", "teapot", "anchor", "violin", "marble",
          "feather", "chimney", "ladder", "biscuit", "walrus", "tulip")
_VERBS = ("whistles", "forgets", "borrows", "paints", "swallows", "follows",
          "measures", "rescues", "tickles", "unfolds")
_ADJECTIVES = ("crimson", "hollow", "sleepy", "brittle", "tangled", "velvet",
               "frozen", "noisy", "gentle", "crooked")
_NEUTRAL = (
    "what happened", "tell me more", "what is the status", "who is responsible",
    "when was it decided", "why did it change", "how does it work", "what is next",
    "is it done", "what did we agree", "where is it kept", "what was the result",
    "what should I remember", "give me the details", "what changed recently",
    "what is the plan",
)


class _Stream:
    """Deterministic integers from a label (sha256 counter mode)."""

    def __init__(self, label: str) -> None:
        self._label = label.encode("utf-8")
        self._n = 0

    def below(self, n: int) -> int:
        digest = hashlib.sha256(self._label + self._n.to_bytes(8, "big")).digest()
        self._n += 1
        return int.from_bytes(digest[:4], "big") % n


def probe_texts(version: str = PROBE_SET_VERSION) -> tuple[str, ...]:
    """The fixed probe queries for ``version``: 96 texts, same on every machine."""
    if version != "hub-probes-v1":
        raise ValueError(f"unknown probe set {version!r}")
    s = _Stream(version)
    probes: list[str] = []
    for _ in range(32):  # random syllable words
        words = ["".join(_SYLLABLES[s.below(len(_SYLLABLES))] for _ in range(2 + s.below(2)))
                 for _ in range(3)]
        probes.append(" ".join(words))
    for _ in range(32):  # ordinary words, no meaning
        probes.append(f"{_ADJECTIVES[s.below(10)]} {_NOUNS[s.below(20)]} "
                      f"{_VERBS[s.below(10)]} the {_NOUNS[s.below(20)]}")
    hexdigits = "0123456789abcdef"
    for i in range(16):  # id-like strings
        h = "".join(hexdigits[s.below(16)] for _ in range(32))
        probes.append((f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}",
                       f"id_{h[:20]}", f"0x{h[:16]}", f"X{h[:6].upper()}-{h[6:10]}")[i % 4])
    probes.extend(_NEUTRAL)
    return tuple(probes)


def space_of(embedder: Any) -> str | None:
    """The embedding space an embedder writes into (model and width)."""
    config = getattr(embedder, "_config", None)
    if config is None or not getattr(config, "model_name", ""):
        return None
    try:
        from superlocalmemory.storage.embedding_migrator import _normalize_signature
        from superlocalmemory.storage.embedding_spaces import signature_of

        return _normalize_signature(signature_of(config))
    except Exception:  # an unreadable config is "no space", never an error
        return None


def _unit_rows(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return m / norms


class ProbeBank:
    """Probe vectors, embedded once per (space, probe version) per process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], np.ndarray] = {}

    def cached(self, space: str | None,
               version: str = PROBE_SET_VERSION) -> np.ndarray | None:
        """The probe vectors when already embedded in this process; never embeds
        (recall reads this, so it can never wait on the model for probes)."""
        if space is None:
            return None
        with self._lock:
            return self._cache.get((space, version))

    def matrix(self, embedder: Any, space: str,
               version: str = PROBE_SET_VERSION) -> np.ndarray | None:
        cached = self.cached(space, version)
        if cached is not None:
            return cached
        texts = list(probe_texts(version))
        vectors = embedder.embed_batch(texts)
        if not vectors or any(v is None for v in vectors):
            return None
        m = _unit_rows(vectors)
        with self._lock:
            self._cache[(space, version)] = m
        return m


PROBES = ProbeBank()


#: When a recall last tried to embed the probes itself (``lazy_warm``).
_LAST_LAZY_WARM = [0.0]
LAZY_WARM_EVERY_S = 60.0


def lazy_warm(embedder: Any) -> bool:
    """From recall, the first time the embedder answered a question in this
    process: embed the probes now (once; a failure is retried after a minute),
    so no process -- a one-shot CLI, an MCP server without the daemon -- stays
    without them."""
    now = time.monotonic()
    if now - _LAST_LAZY_WARM[0] < LAZY_WARM_EVERY_S:
        return False
    _LAST_LAZY_WARM[0] = now
    return warm_probes(embedder)


def warm_probes(embedder: Any) -> bool:
    """Embed the probes for ``embedder``'s space now, once per process, so recall
    can read them (``ProbeBank.cached``). The daemon calls this at boot, the
    background pass and every save get it for free. Never raises."""
    space = space_of(embedder) if embedder is not None else None
    if space is None:
        return False
    try:
        return PROBES.matrix(embedder, space) is not None
    except Exception as exc:  # the model may be gone; recall then imputes
        logger.debug("hub probes not embedded: %s", exc)
        return False


def embed_probes(embedder: Any) -> np.ndarray | None:
    """The probe vectors for ``embedder``, embedded now and not cached: a
    re-index measures a space that is not live yet, so the process-wide bank
    (keyed by the live space) must not learn it. None when the model cannot."""
    try:
        vectors = embedder.embed_batch(list(probe_texts()))
    except Exception as exc:  # a switch is never failed for an accuracy aid
        logger.warning("hub probes could not be embedded for the new model: %s", exc)
        return None
    if not vectors or any(v is None for v in vectors):
        return None
    return _unit_rows(vectors)


def baselines(vectors: Sequence[Sequence[float]], probes: np.ndarray,
              top_k: int = TOP_K) -> list[tuple[float, float]]:
    """``(mean over all probes, mean of the top_k closest)`` per vector."""
    if len(vectors) == 0:
        return []
    sims = _unit_rows(vectors) @ probes.T  # (n, probes)
    k = max(1, min(top_k, sims.shape[1]))
    top = np.sort(sims, axis=1)[:, -k:]
    return [(float(a), float(b)) for a, b in zip(sims.mean(axis=1), top.mean(axis=1))]


def _unit(vector: Sequence[float] | None, probes: np.ndarray | None) -> np.ndarray | None:
    if vector is None or probes is None or len(vector) != probes.shape[1]:
        return None
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return None if norm == 0.0 else v / norm


def baseline_of(vector: Sequence[float] | None, probes: np.ndarray | None,
                top_k: int = TOP_K) -> tuple[float, float] | None:
    """``(top-k mean, mean)`` for one stored vector, the order ``read`` uses: a
    memory the background pass has not reached yet is measured on the spot."""
    unit = _unit(vector, probes)
    if unit is None:
        return None
    (mean, top), = baselines([unit], probes, top_k)
    return top, mean


def query_profile(vector: Sequence[float] | None, probes: np.ndarray | None) -> float | None:
    """A question's mean cosine to the meaningless-sentence probes, or None."""
    unit = _unit(vector, probes)
    if unit is None:
        return None
    return float((probes[NONSENSE_SENTENCES] @ unit).mean())


# -- storage --------------------------------------------------------------------

def _decode(raw: Any) -> list[float] | None:
    from superlocalmemory.storage.embedding_codec import decode_embedding

    try:
        return decode_embedding(raw)
    except Exception:
        return None


def _write(db: Any, rows: list[tuple[str, str, str, str, float, float]]) -> None:
    if not rows:
        return
    now = time.time()
    with db.transaction():
        for fact_id, profile_id, space, version, mean, topk in rows:
            db.execute(
                f"INSERT INTO {TABLE} (fact_id, profile_id, space, probe_version, "
                "baseline_mean, baseline_topk, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(fact_id) DO UPDATE SET profile_id = excluded.profile_id, "
                "space = excluded.space, probe_version = excluded.probe_version, "
                "baseline_mean = excluded.baseline_mean, "
                "baseline_topk = excluded.baseline_topk, computed_at = excluded.computed_at",
                (fact_id, profile_id, space, version, mean, topk, now),
            )


def ensure_reembed_trigger(db: Any) -> bool:
    """Rebind the re-embed trigger to the live vector column; True if it had to
    be. A switch before 4.2.2 left it on ``embedding_next``, where the clear of
    the replaced vectors deleted every baseline (storage/hub_baseline_trigger)."""
    from superlocalmemory.storage import hub_baseline_trigger as trigger

    try:
        rows = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (trigger.NAME,))
        if rows and trigger.is_bound(rows[0]["sql"]):
            return False
        with db.raw_connection() as conn:  # commits on exit
            return trigger.rebind(conn)
    except Exception as exc:  # fail-open: the baselines are an aid, not a gate
        logger.debug("hub baseline trigger check skipped: %s", exc)
        return False


def table_ready(db: Any) -> bool:
    try:
        return bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)))
    except Exception:
        return False


def compute_for_facts(db: Any, embedder: Any, fact_ids: Sequence[str]) -> int:
    """Store baselines for these facts now; the number written. Fail-open."""
    if not fact_ids or embedder is None:
        return 0
    try:
        space = space_of(embedder)
        if space is None or not table_ready(db):
            return 0
        marks = ",".join("?" for _ in fact_ids)
        found = db.execute(
            f"SELECT fact_id, profile_id, embedding FROM atomic_facts "
            f"WHERE fact_id IN ({marks}) AND embedding IS NOT NULL", tuple(fact_ids))
        return _compute_rows(db, embedder, space, found)
    except Exception as exc:
        logger.debug("hub baseline at save skipped: %s", exc)
        return 0


def _compute_rows(db: Any, embedder: Any, space: str, found: list) -> int:
    rows = [(r["fact_id"], r["profile_id"], _decode(r["embedding"])) for r in found]
    rows = [r for r in rows if r[2]]
    if not rows:
        return 0
    probes = PROBES.matrix(embedder, space)
    if probes is None:
        return 0
    width = probes.shape[1]
    rows = [r for r in rows if len(r[2]) == width]  # a vector from another space
    values = baselines([r[2] for r in rows], probes)
    _write(db, [(fid, pid, space, PROBE_SET_VERSION, mean, topk)
                for (fid, pid, _), (mean, topk) in zip(rows, values)])
    return len(rows)


def read(db: Any, fact_ids: Sequence[str], space: str | None) -> dict[str, tuple[float, float]]:
    """``fact_id -> (baseline_topk, baseline_mean)`` for the live space; {} when absent."""
    if not fact_ids or space is None:
        return {}
    try:
        marks = ",".join("?" for _ in fact_ids)
        rows = db.execute(
            f"SELECT fact_id, baseline_topk, baseline_mean FROM {TABLE} "
            f"WHERE fact_id IN ({marks}) AND space = ? AND probe_version = ?",
            (*fact_ids, space, PROBE_SET_VERSION))
    except Exception:  # no table yet (migration pending): nothing recorded
        return {}
    return {r["fact_id"]: (float(r["baseline_topk"]), float(r["baseline_mean"])) for r in rows}


def pending(db: Any, space: str, limit: int, after_rowid: int = 0) -> list:
    """Facts with a vector but no baseline for this space and probe version,
    after ``after_rowid`` (the pass's cursor)."""
    return db.execute(
        f"SELECT af.rowid AS rid, af.fact_id, af.profile_id, af.embedding FROM atomic_facts af "
        f"LEFT JOIN {TABLE} b ON b.fact_id = af.fact_id AND b.space = ? "
        "AND b.probe_version = ? WHERE af.embedding IS NOT NULL AND b.fact_id IS NULL "
        "AND af.rowid > ? ORDER BY af.rowid LIMIT ?",
        (space, PROBE_SET_VERSION, int(after_rowid), int(limit)))


def backfill_batch(db: Any, embedder: Any, batch: int = BATCH,
                   after_rowid: int = 0) -> tuple[int, int | None]:
    """One bounded batch after the cursor: ``(written, next cursor)``; the
    cursor is None when nothing remains. Rows that cannot be measured (a vector
    from another space) are stepped past, so they never stall the pass."""
    space = space_of(embedder)
    if space is None or not table_ready(db):
        return 0, None
    found = pending(db, space, batch, after_rowid)
    if not found:
        return 0, None
    written = _compute_rows(db, embedder, space, found)
    return written, int(dict(found[-1])["rid"])


def backfill_step(db: Any, embedder: Any, batch: int = BATCH) -> int:
    """Compute one bounded batch of missing baselines; 0 when none remain."""
    return backfill_batch(db, embedder, batch)[0]


class Backfill:
    """The pass that gives an existing store its baselines.

    Bounded (``batch`` facts per step, at most ``max_batches`` steps per run),
    resumable (each step re-reads what is missing, so a stop at any point loses
    nothing). The maintenance scheduler runs it shortly after start and then
    every cycle (``core/maintenance_scheduler.py``) and stops it on close.
    """

    def __init__(self, db: Any, embedder: Any, *, batch: int = BATCH) -> None:
        self._db = db
        self._embedder = embedder
        self._batch = batch
        self._stop = threading.Event()
        self.written = 0
        self.finished = False

    def stop(self, timeout: float = 0.0) -> None:
        """Stop after the batch in progress (``timeout`` kept for symmetry)."""
        self._stop.set()

    def run_until_done(self, max_batches: int | None = None) -> int:
        """Run in the calling thread until nothing is missing, ``max_batches``
        batches have run, or :meth:`stop`; return the facts measured. Each batch
        yields the shared embedder to recall (``recall_gate.background_work``)."""
        from superlocalmemory.core.recall_gate import background_work

        ensure_reembed_trigger(self._db)
        batches, cursor = 0, 0
        while not self._stop.is_set() and (max_batches is None or batches < max_batches):
            with background_work():
                n, cursor = backfill_batch(self._db, self._embedder, self._batch, cursor)
            batches += 1
            self.written += n
            if cursor is None:
                self.finished = True
                break
        return self.written
