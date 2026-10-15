# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Recall latency while memories are being written, on a store of real size.

WHAT IT MEASURES
----------------
On the owner's store (~26k facts, ~3.5k entities, ~25k graph nodes, ~600k
edges) every write made the next recall rebuild the entity maps, the graph
metrics and the adjacency cache: 2.3 s quiet, 5+ s under load, while Mode B
enrichment wrote every few seconds. This script builds a synthetic store of
that shape in a temporary directory and measures, through ``engine.recall``:

1. ``quiet``   -- recall p50/p95 with nothing being written;
2. ``writer``  -- the same with one write every ``--write-interval`` seconds
   (a new fact with links, every 5th an enrichment update of an old fact,
   every 10th a hard delete);
3. ``cycles``  -- ``--cycles`` back-to-back write/recall cycles, sampling the
   process RSS, to show memory stays bounded.

It reports the entity-graph channel's status per recall too, so a "fast" run
that simply skipped the graph (``warming``) cannot pass for a fast graph.

ISOLATION
---------
Never touches a live install: HOME, the data root and the daemon port are
pointed at the temporary directory BEFORE the package is imported, and an
audit hook refuses any open() under the real ``~/.superlocalmemory`` and any
connect() to the live daemon ports. Heavy models are not loaded: the embedder
is a deterministic hash embedder and the reranker/worker pool are stubbed, as
in the test suite.

USAGE
-----
    uv run --frozen python scripts/perf/recall_under_writes.py \
        --facts 26000 --entities 3500 --edges 600000 --json out.json

Run it against another checkout with ``PYTHONPATH=<checkout>/src``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_REAL_HOME = Path.home().resolve()
_LIVE_ROOT = str(_REAL_HOME / ".superlocalmemory")
_LIVE_PORTS = {8765, 8767}


def _audit(event: str, args: tuple) -> None:
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        if path.startswith(_LIVE_ROOT):
            raise PermissionError(f"benchmark refused the live store: {path}")
    if event == "socket.connect" and len(args) >= 2:
        addr = args[1]
        if isinstance(addr, tuple) and len(addr) >= 2 and addr[1] in _LIVE_PORTS:
            raise PermissionError(f"benchmark refused live daemon port {addr[1]}")


def isolate(root: Path) -> None:
    """Point every path and port the product resolves at ``root``."""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "SLM_DATA_DIR": str(root / "data"), "SL_MEMORY_PATH": str(root / "data"),
        "SLM_HOME": str(root / "data"),
        "SLM_DAEMON_PORT": str(20_000 + secrets.randbelow(40_000)),
    }
    os.environ.update(env)
    for key in ("SLM_HOOK_DAEMON_URL", "SLM_MESH_PEER_URL", "SLM_DAEMON_HOST", "SLM_HOST"):
        os.environ.pop(key, None)
    sys.addaudithook(_audit)


# --------------------------------------------------------------------------
# Synthetic store
# --------------------------------------------------------------------------

_SYL = ("ka", "lo", "vir", "ten", "mo", "ras", "qui", "dal", "fen", "zo", "bri", "nek",
        "sha", "tor", "mel", "gu", "vash", "pil", "ord", "cen", "lum", "jar", "ix", "wen")
_TOPICS = ("deployment", "budget", "migration", "roadmap", "incident", "contract",
           "release", "benchmark", "hiring", "latency", "outage", "design", "audit")
DIM = 768


def entity_names(n: int, rnd: random.Random) -> list[str]:
    names: set[str] = set()
    while len(names) < n:
        word = "".join(rnd.choice(_SYL) for _ in range(rnd.randint(2, 3)))
        names.add(word.capitalize())
    return sorted(names)


def _vec(rng: Any, center: Any) -> bytes:
    import numpy as np

    v = center + 0.8 * rng.standard_normal(DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32).tobytes()


def _fact_row(i: int, fid: str, ents: list[str], names: dict[str, str],
              emb: bytes) -> tuple:
    topic = _TOPICS[i % len(_TOPICS)]
    who = [names[e] for e in ents]
    text = f"{who[0]} talked about the {topic} " + " and ".join(who[1:]) if len(who) > 1 \
        else f"{who[0]} owns the {topic} work item {i}" if who else f"note {i} on {topic}"
    return (fid, f"m_{fid}", "default", text, json.dumps(who), json.dumps(ents), emb,
            f"+{i} seconds")


def _pick_entities(rnd: random.Random, entity_ids: list[str]) -> list[str]:
    # Zipf-ish: a few entities name many facts, most name a handful.
    k = rnd.choice((0, 1, 1, 2, 2, 3))
    out: list[str] = []
    for _ in range(k):
        idx = min(int(rnd.paretovariate(1.1)) - 1, len(entity_ids) - 1)
        eid = entity_ids[(idx * 7919 + rnd.randrange(3)) % len(entity_ids)]
        if eid not in out:
            out.append(eid)
    return out


_FACT_SQL = (
    "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, entities_json,"
    " canonical_entities_json, embedding, fact_type, confidence, importance,"
    " evidence_count, access_count, created_at) VALUES (?,?,?,?,?,?,?, 'semantic',"
    " 0.9, 0.5, 1, 0, datetime('2026-01-01', ?))")
_EDGE_SQL = (
    "INSERT INTO graph_edges (edge_id, profile_id, source_id, target_id, edge_type,"
    " weight, created_at) VALUES (?, 'default', ?, ?, ?, ?, '2026-01-01')")


def build_store(db_path: Path, *, n_facts: int, n_entities: int, n_edges: int,
                seed: int = 7) -> dict[str, Any]:
    """Bulk-load the store; returns the ids the workload needs."""
    import sqlite3

    import numpy as np

    rnd, rng = random.Random(seed), np.random.default_rng(seed)
    names_list = entity_names(n_entities, rnd)
    entity_ids = [f"ent{i:05d}" for i in range(n_entities)]
    names = dict(zip(entity_ids, names_list))
    centers = rng.standard_normal((32, DIM)).astype(np.float32)
    fact_ids = [f"f{i:06d}" for i in range(n_facts)]
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA cache_size = -500000")  # the bulk load is not measured
    edge_triggers = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'graph_edges'")]
    for name in edge_triggers:  # re-created below; 4x faster bulk load
        conn.execute(f"DROP TRIGGER {name}")
    with conn:
        conn.executemany(
            "INSERT INTO canonical_entities (entity_id, profile_id, canonical_name,"
            " entity_type, fact_count) VALUES (?, 'default', ?, 'person', 0)",
            list(names.items()))
        conn.executemany("INSERT INTO memories (memory_id, profile_id, content)"
                         " VALUES (?, 'default', 'seed')", [(f"m_{f}",) for f in fact_ids])
        conn.executemany(_FACT_SQL, [
            _fact_row(i, fid, _pick_entities(rnd, entity_ids), names,
                      _vec(rng, centers[i % 32])) for i, fid in enumerate(fact_ids)])
        conn.executemany(_EDGE_SQL, _edges(rnd, fact_ids, n_edges))
        conn.executemany(
            "INSERT INTO fact_importance (fact_id, profile_id, pagerank_score,"
            " community_id, degree_centrality) VALUES (?, 'default', ?, ?, ?)",
            [(f, rnd.random() / n_facts * 4, i // 400, rnd.random() / 10)
             for i, f in enumerate(fact_ids)])
    from superlocalmemory.storage import graph_generation

    conn.executescript(graph_generation.DDL)
    conn.close()
    return {"entity_ids": entity_ids, "names": names, "fact_ids": fact_ids}


def index_vectors(engine: Any, db_path: Path) -> int:
    """Project every stored vector into the KNN table, as the store pipeline does."""
    import sqlite3

    import numpy as np

    store = getattr(engine, "_vector_store", None)
    if store is None or not getattr(store, "available", False):
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT fact_id, embedding FROM atomic_facts").fetchall()
    finally:
        conn.close()
    return sum(bool(store.upsert(fid, "default", np.frombuffer(emb, dtype=np.float32).tolist()))
               for fid, emb in rows)


def _edges(rnd: random.Random, fact_ids: list[str], n_edges: int):
    n = len(fact_ids)
    for k in range(n_edges):
        a = rnd.randrange(n)
        # Mostly local links (communities), some long-range.
        b = (a + rnd.randint(1, 400)) % n if rnd.random() < 0.85 else rnd.randrange(n)
        etype = "entity" if k % 3 else "semantic"
        yield (f"e{k:07d}", fact_ids[a], fact_ids[b], etype, 0.3 + 0.7 * rnd.random())


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class HashEmbedder:
    """Deterministic 768-d embedder; no model is loaded."""

    is_available = True
    dimension = DIM

    def embed(self, text: str) -> list[float]:
        import numpy as np

        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(DIM).astype(np.float32)
        return (v / np.linalg.norm(v)).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def compute_fisher_params(self, _emb: Any) -> tuple[list[float], list[float]]:
        return [0.0] * DIM, [1.0] * DIM


def _stub_heavy_models() -> list[Any]:
    from unittest.mock import MagicMock, patch

    reranker = MagicMock()
    reranker.rerank.return_value = None
    reranker.warmup_sync.return_value = True
    pool = MagicMock()
    pool.recall.return_value = {"ok": True, "results": [], "count": 0}
    patches = [
        patch("superlocalmemory.retrieval.reranker.CrossEncoderReranker",
              return_value=reranker),
        patch("superlocalmemory.core.worker_pool.WorkerPool.shared", return_value=pool),
        patch("superlocalmemory.core.engine_wiring.init_sufficiency_judge",
              return_value=None),
    ]
    for p in patches:
        p.start()
    return patches


def make_engine(base_dir: Path) -> Any:
    from unittest.mock import patch

    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine
    from superlocalmemory.storage.models import Mode

    config = SLMConfig.for_mode(Mode.A, base_dir=base_dir)
    config.retrieval.use_cross_encoder = False
    engine = MemoryEngine(config)
    embedder = HashEmbedder()
    with patch("superlocalmemory.core.engine_wiring.init_embedder", return_value=embedder):
        engine.initialize()
    engine._embedder = embedder
    return engine


def daemon_policy(engine: Any) -> None:
    """What the daemon does at start: build the graph, never on a recall."""
    from superlocalmemory.retrieval import adjacency_rcu, entity_graph_warmup

    adjacency_rcu.prefer_background_builds(entity_graph_warmup.channel_of(engine))
    entity_graph_warmup.warm(engine, "default")


# --------------------------------------------------------------------------
# Workload
# --------------------------------------------------------------------------

class Writer:
    """Writes like the store pipeline + Mode B enrichment, from its own thread."""

    def __init__(self, db_path: Path, ids: dict[str, Any], seed: int = 11,
                 vector_store: Any = None) -> None:
        self._db_path = db_path
        self._vs = vector_store
        self._ids = ids
        self._rnd = random.Random(seed)
        self._n = 0
        self.deleted: list[str] = []
        self.written: list[str] = []
        self.errors: list[str] = []

    def write_once(self) -> None:
        import sqlite3

        conn = sqlite3.connect(str(self._db_path), timeout=30)
        try:
            with conn:
                self._n += 1
                self._insert(conn)
                if self._n % 5 == 0:
                    self._enrich_old(conn)
                if self._n % 10 == 0:
                    self._delete(conn)
        except Exception as exc:  # noqa: BLE001 -- reported, not hidden
            self.errors.append(repr(exc))
        finally:
            conn.close()
        self._project_vectors()

    def _project_vectors(self) -> None:
        import numpy as np

        if self._vs is None:
            return
        fid = self.written[-1] if self.written else None
        if fid is not None:
            emb = _vec(np.random.default_rng(self._n), np.zeros(DIM, dtype=np.float32))
            self._vs.upsert(fid, "default", np.frombuffer(emb, dtype=np.float32).tolist())
        if self._n % 10 == 0 and self.deleted:
            self._vs.delete(self.deleted[-1])

    def _insert(self, conn: Any) -> None:
        import numpy as np

        rnd, ids = self._rnd, self._ids
        fid = f"w{self._n:06d}"
        ents = _pick_entities(rnd, ids["entity_ids"]) or [ids["entity_ids"][0]]
        emb = _vec(np.random.default_rng(self._n), np.zeros(DIM, dtype=np.float32))
        conn.execute("INSERT INTO memories (memory_id, profile_id, content)"
                     " VALUES (?, 'default', 'w')", (f"m_{fid}",))
        row = list(_fact_row(self._n, fid, ents, ids["names"], emb))
        row[-1] = "+0 seconds"
        conn.execute(_FACT_SQL.replace("datetime('2026-01-01', ?)", "datetime('now', ?)"),
                     row)
        pool = ids["fact_ids"]
        conn.executemany(_EDGE_SQL, [
            (f"we{self._n}_{k}", fid, rnd.choice(pool), "entity", 0.5 + rnd.random() / 2)
            for k in range(20)])
        self.written.append(fid)

    def _enrich_old(self, conn: Any) -> None:
        rnd, ids = self._rnd, self._ids
        fid = rnd.choice(ids["fact_ids"])
        ents = _pick_entities(rnd, ids["entity_ids"])
        conn.execute("UPDATE atomic_facts SET canonical_entities_json = ? WHERE fact_id = ?",
                     (json.dumps(ents), fid))

    def _delete(self, conn: Any) -> None:
        pool = [f for f in self._ids["fact_ids"] if f not in set(self.deleted)]
        fid = self._rnd.choice(pool)
        conn.execute("DELETE FROM graph_edges WHERE source_id = ? OR target_id = ?",
                     (fid, fid))
        conn.execute("DELETE FROM fact_importance WHERE fact_id = ?", (fid,))
        conn.execute("DELETE FROM atomic_facts WHERE fact_id = ?", (fid,))
        self.deleted.append(fid)


def queries(ids: dict[str, Any], n: int, seed: int = 5) -> list[str]:
    rnd = random.Random(seed)
    names = list(ids["names"].values())
    heavy = names[:60]  # low ids carry the most facts (see _pick_entities)
    out = []
    for i in range(n):
        a, b = rnd.choice(heavy), rnd.choice(names)
        out.append(f"What did {a} say to {b} about the {_TOPICS[i % len(_TOPICS)]}?")
    return out


def rss_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / (1024 * 1024)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * len(s) + 0.5)) - 1))]


def one_recall(engine: Any, query: str, stats: dict[str, Any]) -> None:
    t0 = time.perf_counter()
    resp = engine.recall(query, limit=10)
    ms = (time.perf_counter() - t0) * 1000.0
    stats["ms"].append(ms)
    status = (getattr(resp, "channel_status", {}) or {}).get("entity_graph", "?")
    stats["status"][status] = stats["status"].get(status, 0) + 1
    lag = (getattr(resp, "graph_lag", None)
           or (getattr(resp, "channel_status_detail", {}) or {}).get("graph_lag_s"))
    if lag is not None:
        stats.setdefault("lag", []).append(float(lag))
    deleted = stats.get("deleted")
    if deleted:
        returned = {r.fact.fact_id for r in getattr(resp, "results", []) if hasattr(r, "fact")}
        stats["deleted_returned"] += len(returned & set(deleted))


def summarise(stats: dict[str, Any]) -> dict[str, Any]:
    ms = stats["ms"]
    out = {"n": len(ms), "p50_ms": round(_pct(ms, 0.50), 1),
           "p95_ms": round(_pct(ms, 0.95), 1), "max_ms": round(max(ms), 1) if ms else None,
           "entity_graph_status": stats["status"]}
    if stats.get("lag"):
        out["graph_lag_s_max"] = round(max(stats["lag"]), 2)
    if "deleted_returned" in stats:
        out["deleted_returned"] = stats["deleted_returned"]
    return out


def _new_stats(deleted: list[str] | None = None) -> dict[str, Any]:
    stats: dict[str, Any] = {"ms": [], "status": {}}
    if deleted is not None:
        stats["deleted"], stats["deleted_returned"] = deleted, 0
    return stats


def phase_quiet(engine: Any, qs: list[str], n: int) -> dict[str, Any]:
    stats = _new_stats()
    for i in range(n):
        one_recall(engine, qs[i % len(qs)], stats)
    return summarise(stats)


def phase_writer(engine: Any, qs: list[str], writer: Writer, *, seconds: float,
                 interval: float, pause: float) -> dict[str, Any]:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            writer.write_once()

    thread = threading.Thread(target=loop, name="bench-writer", daemon=True)
    thread.start()
    stats = _new_stats(writer.deleted)
    end, i = time.monotonic() + seconds, 0
    try:
        while time.monotonic() < end:
            one_recall(engine, qs[i % len(qs)], stats)
            i += 1
            time.sleep(pause)
    finally:
        stop.set()
        thread.join(timeout=30)
    out = summarise(stats)
    out["writes"] = writer._n
    out["writer_errors"] = len(writer.errors)
    return out


def phase_cycles(engine: Any, qs: list[str], writer: Writer, cycles: int) -> dict[str, Any]:
    stats = _new_stats(writer.deleted)
    samples = [round(rss_mb(), 1)]
    for i in range(cycles):
        writer.write_once()
        one_recall(engine, qs[i % len(qs)], stats)
        if (i + 1) % max(1, cycles // 10) == 0:
            samples.append(round(rss_mb(), 1))
    out = summarise(stats)
    out.update({"rss_mb_samples": samples, "rss_mb_start": samples[0],
                "rss_mb_end": samples[-1]})
    return out


def run(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    patches = _stub_heavy_models()
    try:
        engine = make_engine(root / "data")
        db_path = Path(engine._config.db_path)
        t0 = time.monotonic()
        ids = build_store(db_path, n_facts=args.facts, n_entities=args.entities,
                          n_edges=args.edges)
        indexed = index_vectors(engine, db_path)
        build_s = time.monotonic() - t0
        qs = queries(ids, 40)
        t0 = time.monotonic()
        daemon_policy(engine)
        for q in qs[:3]:  # the daemon's own warm-up recalls
            engine.recall(q, limit=10)
        warm_s = time.monotonic() - t0
        report: dict[str, Any] = {
            "store": {"facts": args.facts, "entities": args.entities, "edges": args.edges,
                      "vectors_indexed": indexed,
                      "build_s": round(build_s, 1), "warm_s": round(warm_s, 1)},
            "rss_mb_after_warm": round(rss_mb(), 1),
        }
        report["quiet"] = phase_quiet(engine, qs, args.recalls)
        writer = Writer(db_path, ids, vector_store=getattr(engine, "_vector_store", None))
        report["writer"] = phase_writer(engine, qs, writer, seconds=args.writer_seconds,
                                        interval=args.write_interval, pause=args.pause)
        report["cycles"] = phase_cycles(engine, qs, writer, args.cycles)
        engine.close()
        return report
    finally:
        for p in patches:
            p.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--facts", type=int, default=26_000)
    ap.add_argument("--entities", type=int, default=3_500)
    ap.add_argument("--edges", type=int, default=600_000)
    ap.add_argument("--recalls", type=int, default=60)
    ap.add_argument("--writer-seconds", type=float, default=90.0)
    ap.add_argument("--write-interval", type=float, default=2.0)
    ap.add_argument("--pause", type=float, default=0.25)
    ap.add_argument("--cycles", type=int, default=1000)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="slm-perf-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp).resolve()
        isolate(root)
        report = run(args, root)
        report["label"] = args.label
        text = json.dumps(report, indent=2)
        print(text, flush=True)
        if args.json:
            args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
