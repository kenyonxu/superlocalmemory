"""Model-free synthetic recall profiler; never reads an existing store.

Run with PYTHONPATH=src uv run --frozen python benchmark/recall_channels.py
--directory "$TMPDIR/recall-channels" --facts 30000 --samples 20 --label before.
The directory must be new on the first run. Output includes cProfile and SQL plans.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import math
import os
import re
import statistics
import time
from pathlib import Path

from superlocalmemory.core.config import RetrievalConfig
from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.retrieval.engine import RetrievalEngine
from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.retrieval.spreading_activation import SpreadingActivation
from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.embedding_codec import encode_embedding, encode_float_vector


def seed(db: DatabaseManager, count: int, *, vectors: bool = False, migrated: bool = False) -> None:
    """Deterministic SQL seeder using the product schema, indexes and triggers."""
    db.initialize(schema)
    if migrated:
        migrate(db)
    vector = encode_embedding([0.01] * 768) if vectors else None
    fisher = encode_float_vector([0.01] * 768) if vectors else None
    with db.transaction():
        conn = db._txn_state.conn
        for profile in ("default", "team", "other"):
            conn.execute("INSERT OR IGNORE INTO profiles(profile_id, name) VALUES (?, ?)",
                         (profile, profile))
            conn.execute("INSERT INTO memories(memory_id, profile_id, content) VALUES (?, ?, ?)",
                         (profile, profile, "synthetic"))
        conn.executemany(
            "INSERT INTO canonical_entities(entity_id, canonical_name, profile_id) "
            "VALUES (?, ?, ?)",
            ((f"e{i}", "Alice" if i in (0, 8, 9) else f"Project{i}", owner(i))
             for i in range(count // 2)),
        )
        conn.executemany(
            "INSERT INTO atomic_facts(fact_id, memory_id, profile_id, scope, shared_with, "
            "content, canonical_entities_json, created_at,embedding,fisher_mean,fisher_variance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ((*fact_row(i, count), vector, fisher, fisher) for i in range(count)),
        )
        seed_graph_and_events(conn, count)


def migrate(db: DatabaseManager) -> None:
    from superlocalmemory.storage import migration_runner

    learning = db.db_path.parent / "synthetic-learning.db"
    for suffix in ("", "-wal", "-shm"):
        candidate = learning.with_name(learning.name + suffix)
        if candidate.exists() or candidate.is_symlink():
            raise ValueError("first seed must not open an existing learning sidecar")
    for apply in (migration_runner.apply_all, migration_runner.apply_deferred):
        result = apply(learning, db.db_path)
        if result["failed"]:
            raise RuntimeError(f"synthetic migrations failed: {result['failed']}")
    db.initialize(schema)


def owner(i: int) -> str:
    return "default" if i % 10 < 8 else ("team" if i % 10 == 8 else "other")


def seed_graph_and_events(conn, count: int) -> None:
    conn.executemany(
        "INSERT INTO temporal_events(event_id,entity_id,fact_id,referenced_date,profile_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ((f"t{i}" if position == 0 else f"hub-{i}", eid, f"f{i:06}",
          f"2025-{i % 12 + 1:02}-{i % 28 + 1:02}", owner(i))
         for i in range(count) for position, eid in enumerate(json.loads(fact_row(i, count)[6]))),
    )
    conn.executemany(
        "INSERT INTO graph_edges(edge_id,source_id,target_id,weight,profile_id,edge_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ((f"g{i}-{j}", f"f{i:06}", f"f{(i + j) % count:06}", 0.7, owner(i),
          "contradiction" if i % 97 == 0 and j == 11 else "entity")
         for i in range(count) for j in range(1, 12)),
    )
    conn.executemany(
        "INSERT INTO fact_importance(fact_id,profile_id,pagerank_score,community_id) "
        "VALUES (?, ?, ?, ?)",
        ((f"f{i:06}", owner(i), 1 / count, i % 20) for i in range(count)),
    )


def fact_row(i: int, count: int) -> tuple:
    profile = owner(i)
    scope = "global" if i % 10 == 8 else ("shared" if i % 10 == 9 else "personal")
    entities = [f"e{i % (count // 2)}"]
    if i % 3 == 0:
        entities.append({"default": "e0", "team": "e8", "other": "e9"}[profile])
    return (f"f{i:06}", profile, profile, scope, '["default"]',
            f"Alice project memory release {i % 100} engineering topic{i % 500}",
            json.dumps(list(dict.fromkeys(entities))),
            f"2025-{i % 12 + 1:02}-{i % 28 + 1:02} 12:00:00")


def measure(call, samples: int) -> dict:
    started = time.perf_counter()
    call()
    cold = (time.perf_counter() - started) * 1000
    times = []
    rankings = []
    for _ in range(samples):
        started = time.perf_counter()
        result = call()
        times.append((time.perf_counter() - started) * 1000)
        rankings.append(ranking(result))
    ordered = sorted(times)
    return {"cold_ms": cold, "p50_ms": statistics.median(times),
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1],
            "samples_ms": times, "ranking": rankings[-1]}


def ranking(result):
    if hasattr(result, "results"):
        return [(r.fact.fact_id, r.score, r.channel_scores) for r in result.results]
    if isinstance(result, dict):
        return sorted(result.items())
    return result


def profile_probe(call, directory: Path, label: str) -> None:
    # Python 3.14's cProfile uses one process-wide monitoring slot. Do not
    # install nested profilers in executor workers; direct channel profiles
    # are also emitted for runtimes whose profiler is thread-local.
    profiler = cProfile.Profile()
    result = profiler.runcall(call)
    profiler.dump_stats(str(directory / f"{label}.prof"))
    if getattr(result, "incomplete_channels", None):
        raise RuntimeError("profiled recall lost a channel")


def profile_calls(db, directory, samples, label, channels=None) -> dict:
    entity, temporal, bm25 = channels or (
        EntityGraphChannel(db), TemporalChannel(db), BM25Channel(db))
    engine = RetrievalEngine(db, RetrievalConfig(use_cross_encoder=False, sufficiency_judge="off"),
                             {"entity_graph": entity, "temporal": temporal, "bm25": bm25})
    propagation = SpreadingActivation(db, None)
    calls = {
        "bm25": lambda: bm25.search("Alice memory release", "default"),
        "entity": lambda: entity.search("Alice", "default"),
        "entity_candidates": lambda: entity.score_candidates(
            "Alice", [f"f{i:06}" for i in range(50)], "default"),
        "temporal": lambda: temporal.search("Alice on 2025-03-15T00:00:00", "default"),
        "graph_propagation": lambda: propagation._propagate(
            [(f"f{i:06}", 0.9) for i in range(8)], "default"),
        "recall": lambda: engine.recall("Alice on 2025-03-15T00:00:00 memory release", "default"),
    }
    queries = {}
    execute = db.execute

    def traced(sql, params=()):
        if sql.lstrip().upper().startswith(("SELECT", "WITH")) and sql not in queries:
            queries[sql] = params
        return execute(sql, params)

    output = {}
    db.execute = traced
    try:
        for name, call in calls.items():
            output[name] = measure(call, samples)
            profile_probe(call, directory, f"{label}-{name}")
    finally:
        engine.close(wait=True)
        db.execute = execute
    plans = {sql: [tuple(r) for r in execute("EXPLAIN QUERY PLAN " + sql, params)]
             for sql, params in queries.items()}
    (directory / f"{label}-plans.json").write_text(json.dumps(plans, indent=2))
    return output


def synthetic_path(directory: Path, facts: int) -> tuple[Path, bool]:
    """Reject existing unmarked stores and paths outside TMPDIR before opening SQLite."""
    root = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
    directory = directory.resolve()
    if not directory.is_relative_to(root) or directory == root:
        raise ValueError("profiler directory must be inside TMPDIR")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "synthetic.db"
    if path.is_symlink():
        raise ValueError("synthetic database must not be a symlink")
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.is_symlink() or (not path.exists() and sidecar.exists()):
            raise ValueError("unowned synthetic database sidecar")
    marker = directory / "synthetic-store.json"
    if marker.is_symlink():
        raise ValueError("synthetic manifest must not be a symlink")
    expected = {"seeder": "recall-channels-v2", "facts": facts, "complete": True}
    if path.exists():
        if not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise ValueError("existing database was not seeded by this profiler")
        return path, False
    return path, True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--facts", type=int, default=30000)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--label", default="before")
    args = parser.parse_args()
    if args.facts < 20 or args.samples < 1:
        parser.error("facts must be >= 20 and samples must be >= 1")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.label):
        parser.error("label must contain only letters, numbers, underscores or hyphens")
    path, needs_seed = synthetic_path(args.directory, args.facts)
    db = DatabaseManager(path)
    if needs_seed:
        seed(db, args.facts, vectors=True, migrated=True)
        (path.parent / "synthetic-store.json").write_text(json.dumps(
            {"seeder": "recall-channels-v2", "facts": args.facts, "complete": True}))
    result = profile_calls(db, path.parent, args.samples, args.label)
    (path.parent / f"{args.label}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({name: {k: v for k, v in row.items() if k in {"p50_ms", "p95_ms", "cold_ms"}}
                      for name, row in result.items()}, indent=2))
    db.close()


if __name__ == "__main__":
    main()
