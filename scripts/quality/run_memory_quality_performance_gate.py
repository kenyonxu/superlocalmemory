#!/usr/bin/env python3
"""Generate the redacted SLM 4.0.5 local performance/liveness gate.

This runner deliberately creates a temporary synthetic corpus.  It never
opens the user's SLM data directory and it reports identifiers/counts/timing
only -- never memory content.  It is a release proof, not a benchmark claim
across machines.
"""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _bootstrap_source_tree() -> None:
    """Make the source checkout directly runnable without an editable install."""
    source = Path(__file__).resolve().parents[2] / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


_bootstrap_source_tree()

from superlocalmemory.core.config import RetrievalConfig  # noqa: E402
from superlocalmemory.core.engine_ingestion import build_immediate_admission_handler  # noqa: E402
from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime  # noqa: E402
from superlocalmemory.retrieval.bm25_channel import BM25Channel  # noqa: E402
from superlocalmemory.retrieval.engine import RetrievalEngine  # noqa: E402
from superlocalmemory.storage import schema  # noqa: E402
from superlocalmemory.storage.admission_journal import Actor, RememberRequest  # noqa: E402
from superlocalmemory.storage.database import DatabaseManager  # noqa: E402
import superlocalmemory.storage.migrations.M018_ingestion_operations as m018  # noqa: E402
import superlocalmemory.storage.migrations.M032_write_coordinator_admission as m032  # noqa: E402
import superlocalmemory.storage.migrations.M033_projection_transactions as m033  # noqa: E402
import superlocalmemory.storage.migrations.M034_obligation_integrity as m034  # noqa: E402
import superlocalmemory.storage.migrations.M042_correction_case_ledger as m042  # noqa: E402
from superlocalmemory.storage.models import AtomicFact, MemoryRecord  # noqa: E402

WARMUP_SAMPLES = 5
RECALL_SAMPLES = 50
REMEMBER_SAMPLES = 50
MIXED_READERS = 10
MIXED_WRITERS = 2


@dataclass(frozen=True)
class _GateResources:
    db: DatabaseManager
    engine: RetrievalEngine
    runtime: CanonicalRememberRuntime


def _nearest_rank_ms(samples: list[float], percentile: int) -> float:
    if not samples:
        raise ValueError("performance sample is empty")
    index = max(0, ((len(samples) * percentile + 99) // 100) - 1)
    return sorted(samples)[index] * 1000.0


def _seed_synthetic_corpus(db: DatabaseManager, *, facts: int = 192) -> None:
    """Seed deterministic generated data; no user content is accepted here."""
    for index in range(facts):
        memory_id = f"perf-memory-{index}"
        fact_id = f"perf-fact-{index}"
        db.store_memory(
            MemoryRecord(
                memory_id=memory_id,
                profile_id="default",
                content=f"synthetic performance memory {index}",
            )
        )
        db.store_fact(
            AtomicFact(
                fact_id=fact_id,
                memory_id=memory_id,
                profile_id="default",
                content=(
                    f"synthetic release corpus topic {index % 12} "
                    f"performance retrieval witness {index}"
                ),
            )
        )


def _build_resources(root: Path) -> _GateResources:
    db = DatabaseManager(root / "memory.db")
    db.initialize(schema)
    with db.raw_connection() as conn:
        m018.apply(conn)
        m032.apply(conn)
        m033.apply(conn)
        m034.apply(conn)
        m042.apply(conn)
    _seed_synthetic_corpus(db)
    engine = RetrievalEngine(
        db=db,
        config=RetrievalConfig(use_cross_encoder=False),
        channels={"bm25": BM25Channel(db)},
    )
    runtime = CanonicalRememberRuntime(
        db=db,
        profile_id="default",
        writer=build_immediate_admission_handler(db, profile_id="default"),
        journal_path=root / "admission_journal.db",
        owner_id="quality-performance-gate",
    )
    runtime.start()
    return _GateResources(db=db, engine=engine, runtime=runtime)


def _sample_recall(engine: RetrievalEngine, count: int) -> list[float]:
    samples: list[float] = []
    for index in range(count):
        started = time.monotonic()
        result = engine.recall(
            f"synthetic release topic {index % 12}", "default", limit=10,
        )
        samples.append(time.monotonic() - started)
        if not result.results:
            raise RuntimeError("synthetic recall returned no candidate")
    return samples


def _sample_remember(runtime: CanonicalRememberRuntime) -> list[float]:
    actor = Actor("daemon:quality-gate", frozenset({"default"}), frozenset({"personal"}))
    samples: list[float] = []
    for index in range(REMEMBER_SAMPLES):
        started = time.monotonic()
        receipt = runtime.remember(
            RememberRequest(
                content=f"Synthetic performance acknowledgement witness {index}.",
                profile_id="default",
                source_type="quality_gate",
                idempotency_key=f"quality-remember-{index}",
                trusted_actor_id="daemon:quality-gate",
            ),
            actor,
            deadline_ms=2_000,
        )
        samples.append(time.monotonic() - started)
        if not receipt.payload.get("fact_ids"):
            raise RuntimeError("canonical remember acknowledgement lacked fact ids")
    return samples


def _current_truth_overhead(resources: _GateResources) -> dict[str, float]:
    """Measure the real admission query versus an explicitly bypassed control.

    The bypass lives only in this temporary runner and is restored immediately.
    It establishes the incremental cost of the current-truth checks on the
    identical corpus; it does not alter the shipped retrieval policy.
    """
    db = resources.db
    original_invalidated = db.get_invalidated_fact_ids
    original_nonapplied = db.get_nonapplied_correction_successor_ids
    try:
        db.get_invalidated_fact_ids = lambda *args, **kwargs: set()  # type: ignore[method-assign]
        db.get_nonapplied_correction_successor_ids = lambda *args, **kwargs: set()  # type: ignore[method-assign]
        baseline = _sample_recall(resources.engine, RECALL_SAMPLES)
    finally:
        db.get_invalidated_fact_ids = original_invalidated  # type: ignore[method-assign]
        db.get_nonapplied_correction_successor_ids = original_nonapplied  # type: ignore[method-assign]
    candidate = _sample_recall(resources.engine, RECALL_SAMPLES)
    baseline_p95 = _nearest_rank_ms(baseline, 95)
    candidate_p95 = _nearest_rank_ms(candidate, 95)
    return {
        "baseline_p95_ms": baseline_p95,
        "candidate_p95_ms": candidate_p95,
        "overhead_percent": (
            ((candidate_p95 - baseline_p95) / baseline_p95) * 100.0
            if baseline_p95 > 0
            else 0.0
        ),
    }


def _run_mixed_load(resources: _GateResources, duration_seconds: float) -> dict[str, int]:
    """Run the declared 10-reader/2-writer workload and surface every error."""
    deadline = time.monotonic() + duration_seconds
    start = threading.Barrier(MIXED_READERS + MIXED_WRITERS)
    lock = threading.Lock()
    counters = {"recalls": 0, "remembers": 0, "timeouts": 0, "locks": 0, "errors": 0}
    actor = Actor("daemon:quality-gate", frozenset({"default"}), frozenset({"personal"}))

    def record(name: str) -> None:
        with lock:
            counters[name] += 1

    def record_error(exc: BaseException) -> None:
        message = str(exc).lower()
        if "locked" in message or "busy" in message or "deadlock" in message:
            record("locks")
        elif isinstance(exc, TimeoutError):
            record("timeouts")
        else:
            record("errors")

    def reader(reader_id: int) -> None:
        start.wait(timeout=5)
        while time.monotonic() < deadline:
            try:
                response = resources.engine.recall(
                    f"synthetic release topic {reader_id % 12}", "default", limit=10,
                )
                if not response.results:
                    raise RuntimeError("mixed reader had no synthetic result")
                record("recalls")
            except BaseException as exc:  # workers must report every failure to the gate
                record_error(exc)
                return

    def writer(writer_id: int) -> None:
        sequence = 0
        start.wait(timeout=5)
        while time.monotonic() < deadline:
            try:
                resources.runtime.remember(
                    RememberRequest(
                        content=(
                            "Synthetic mixed-load acknowledgement witness "
                            f"{writer_id}-{sequence}."
                        ),
                        profile_id="default",
                        source_type="quality_gate",
                        idempotency_key=f"quality-mixed-{writer_id}-{sequence}",
                        trusted_actor_id="daemon:quality-gate",
                    ),
                    actor,
                    deadline_ms=2_000,
                )
                record("remembers")
                sequence += 1
                # A bounded write rate keeps the 60-second run representative;
                # it is not a destructive maximum-throughput stress test.
                time.sleep(0.08)
            except BaseException as exc:  # workers must report every failure to the gate
                record_error(exc)
                return

    with ThreadPoolExecutor(max_workers=MIXED_READERS + MIXED_WRITERS) as pool:
        futures = [pool.submit(reader, index) for index in range(MIXED_READERS)]
        futures += [pool.submit(writer, index) for index in range(MIXED_WRITERS)]
        for future in futures:
            future.result(timeout=duration_seconds + 10.0)
    return counters


def run_gate(
    *,
    workdir: Path | None = None,
    mixed_duration_seconds: float = 60.0,
) -> dict[str, Any]:
    """Run the full gate and return a redacted, machine-readable report."""
    if mixed_duration_seconds <= 0:
        raise ValueError("mixed_duration_seconds must be positive")
    owned_dir = workdir is None
    root = workdir or Path(tempfile.mkdtemp(prefix="slm-405-performance-"))
    root.mkdir(parents=True, exist_ok=True)
    resources: _GateResources | None = None
    try:
        resources = _build_resources(root)
        _sample_recall(resources.engine, WARMUP_SAMPLES)
        warm_recall = _sample_recall(resources.engine, RECALL_SAMPLES)
        remember = _sample_remember(resources.runtime)
        overhead = _current_truth_overhead(resources)
        mixed = _run_mixed_load(resources, mixed_duration_seconds)
        report: dict[str, Any] = {
            "contract": "slm.dev/memory-quality-performance/v1",
            "package_version": _package_version(),
            "corpus_revision": "synthetic-v1-192-facts",
            "hardware": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "samples": {
                "warmup": WARMUP_SAMPLES,
                "warm_recalls": len(warm_recall),
                "remember_acknowledgements": len(remember),
                "mixed_duration_seconds": mixed_duration_seconds,
                "mixed_readers": MIXED_READERS,
                "mixed_writers": MIXED_WRITERS,
            },
            "metrics": {
                "warm_recall_p50_ms": _nearest_rank_ms(warm_recall, 50),
                "warm_recall_p95_ms": _nearest_rank_ms(warm_recall, 95),
                "remember_ack_p95_ms": _nearest_rank_ms(remember, 95),
                "current_truth_filter": overhead,
                "mixed": mixed,
            },
        }
        metrics = report["metrics"]
        passed = (
            metrics["warm_recall_p50_ms"] <= 1_000.0
            and metrics["warm_recall_p95_ms"] <= 2_000.0
            and metrics["remember_ack_p95_ms"] <= 2_000.0
            and metrics["current_truth_filter"]["overhead_percent"] <= 10.0
            and mixed["recalls"] > 0
            and mixed["remembers"] > 0
            and mixed["timeouts"] == 0
            and mixed["locks"] == 0
            and mixed["errors"] == 0
        )
        report["passed"] = passed
        return report
    finally:
        if resources is not None:
            resources.runtime.stop()
            resources.engine.close()
        if owned_dir:
            shutil.rmtree(root, ignore_errors=True)


def _package_version() -> str:
    try:
        from superlocalmemory import __version__

        return str(__version__)
    except Exception:
        return "source-checkout"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mixed-duration-seconds", type=float, default=60.0)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_gate(mixed_duration_seconds=args.mixed_duration_seconds)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
