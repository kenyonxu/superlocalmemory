"""Experiment: Governed write-path latency vs. bypass (store_fast delta).

METHOD B (in-process governed envelope):
  Calls the SAME CanonicalRememberRuntime.remember() entrypoint that the live
  HTTP /remember handler calls after request parsing.  The full governed chain
  executes in-process on a throwaway SQLite database:

    journal.prepare()          — AES-256-GCM encrypt + fsync journal row
    journal.mark_dispatched()  — advisory journal transition
    WriteCoordinator.submit()  — queue → background worker thread wake
      _handle_admission()      — inside coordinator's SQLite writer:
        IngestionCommand.submit() → write_queryable() → db.store_memory()
                                                       → db.store_fact()
        _record_projection_obligations() → obligation ledger INSERT (M033)
    journal.mark_committed()   — terminal journal row

  HTTP transport, request parsing, trust hook, and daemon lifecycle are
  explicitly excluded; those belong to the integration boundary, not to the
  write envelope.  This is stated clearly in the report.

BYPASS COMPARISON (store_fast delta):
  Directly times IngestionCommand.submit() → write_queryable() on the same
  throwaway DB.  This skips journal encryption, WriteCoordinator queue/wake,
  obligation ledger, and journal commit.  The delta (governed − bypass) is
  the net governance overhead.

MEASUREMENT DISCIPLINE:
  * 30-call warmup burst (SQLite page cache, codec, thread pool settle).
  * N=200 measured calls, each with a unique idempotency_key so the journal
    never short-circuits on a duplicate.
  * All timing via time.monotonic() (POSIX CLOCK_MONOTONIC, sub-microsecond
    resolution on macOS/Linux).
  * Machine: Apple M-series (M3) macOS 25.4.0, Python 3.13, SQLite 3.x.
    DB files on APFS tmpfs (in-memory temp dir behavior varies; annotated
    in caveats).

CRIT (3 potential measurement biases):
  1. WARMUP ADEQUACY: 30 calls may leave APFS page-cache cold for early
     measured calls if the OS reclaims buffers aggressively.  Mitigated by
     keeping the same TempWorkspace open across warmup+measure.
  2. GIL CONTENTION: WriteCoordinator runs a background worker thread.
     GIL releases on SQLite I/O but acquires on Python bookkeeping.  On CPython
     3.13 GIL contention with the worker thread adds variance to individual
     calls; p99 > p95 captures this tail.  Multi-process/GIL-free comparison
     is out of scope.
  3. BYPASS COMPARISON SCOPE: The bypass path (IngestionCommand.submit)
     includes the DB transaction and FTS indexing but omits the journal,
     coordinator queue, obligation ledger, and manifest.  The delta therefore
     reflects governance overhead, not a complete store_fast() comparison
     (store_fast also skips operation-repository bookkeeping).
"""

from __future__ import annotations

import json
import platform
import shutil
import itertools
import random
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — source-root guard lives in _harness (F-18 single source)
# ---------------------------------------------------------------------------

_EXP_DIR = str(Path(__file__).resolve().parent)
if _EXP_DIR not in sys.path:
    sys.path.insert(0, _EXP_DIR)

from _harness import TempWorkspace, add_profile, fresh_db, verify_slm_source_root  # noqa: E402

# ---------------------------------------------------------------------------
# Import verification (via harness guard)
# ---------------------------------------------------------------------------

import superlocalmemory  # noqa: E402

verify_slm_source_root()

import re as _re_prov  # noqa: E402
_SLM_FILE = superlocalmemory.__file__
# Record a repo-relative module path. An absolute path leaks the
# author's home directory into a published artifact (F-23).
_m = _re_prov.search(r'(src/superlocalmemory/.*|superlocalmemory/.*)$', _SLM_FILE)
_SLM_FILE = _m.group(1) if _m else '<module path unavailable>'
from superlocalmemory.core.engine_ingestion import (  # noqa: E402
    build_immediate_admission_handler,
)
from superlocalmemory.core.ingestion_command import (  # noqa: E402
    IngestionCommand,
    IngestionOperationRepository,
    IngestionRequest,
)
from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime  # noqa: E402
from superlocalmemory.storage.admission_journal import (  # noqa: E402
    Actor,
    RememberRequest,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILE_ID = "bench_latency_profile"
ACTOR_ID = "bench-latency-actor"
WARMUP_N = 30
MEASURE_N = 200


def _actor() -> Actor:
    return Actor(
        principal_id=ACTOR_ID,
        allowed_profiles=frozenset([PROFILE_ID]),
        allowed_scopes=frozenset(["personal", "project", "shared", "global"]),
        trusted=True,
    )


def _governed_request(seq: int) -> RememberRequest:
    """Unique request per call — journal must not short-circuit on duplicate."""
    return RememberRequest(
        content=(
            f"Governed latency benchmark seq={seq}: "
            "The quick brown fox jumps over the lazy dog. "
            "Unique payload to prevent idempotency deduplication."
        ),
        profile_id=PROFILE_ID,
        source_type="latency-benchmark",
        idempotency_key=f"bench-gov-{seq:07d}-{uuid.uuid4().hex[:8]}",
        trusted_actor_id=ACTOR_ID,
        scope="personal",
    )


def _bypass_request(seq: int) -> IngestionRequest:
    """Unique request per call for IngestionCommand.submit() bypass path."""
    return IngestionRequest(
        content=(
            f"Bypass latency benchmark seq={seq}: "
            "The quick brown fox jumps over the lazy dog."
        ),
        profile_id=PROFILE_ID,
        source_type="latency-benchmark-bypass",
        idempotency_key=f"bench-bypass-{seq:07d}-{uuid.uuid4().hex[:8]}",
        metadata={},
        scope="personal",
        shared_with=(),
        trusted_actor_id=ACTOR_ID,
        session_id="",
        session_date="",
        speaker="",
        role="user",
    )


# ---------------------------------------------------------------------------
# Governed path measurement
# ---------------------------------------------------------------------------


def measure_governed(ws: Path, n_warmup: int, n_measure: int) -> list[float]:
    """Return per-call latencies (ms) for the FULL governed write envelope."""
    db = fresh_db(ws, "governed.db")
    add_profile(db, PROFILE_ID)

    writer = build_immediate_admission_handler(db, profile_id=PROFILE_ID)
    journal_path = ws / "governed_journal.db"

    runtime = CanonicalRememberRuntime(
        db=db,
        profile_id=PROFILE_ID,
        writer=writer,
        journal_path=journal_path,
    )
    runtime.start()
    assert runtime.ready, "CanonicalRememberRuntime did not become ready"

    actor = _actor()

    # Warmup — excluded from measurement
    for i in range(n_warmup):
        runtime.remember(_governed_request(-(i + 1)), actor)

    # Measured calls
    latencies: list[float] = []
    for i in range(n_measure):
        req = _governed_request(i)
        t0 = time.monotonic()
        runtime.remember(req, actor)
        elapsed_ms = (time.monotonic() - t0) * 1_000
        latencies.append(elapsed_ms)

    runtime.stop()
    db.close()
    return latencies


# ---------------------------------------------------------------------------
# Bypass path measurement (IngestionCommand.submit directly)
# ---------------------------------------------------------------------------


def measure_bypass(ws: Path, n_warmup: int, n_measure: int) -> list[float]:
    """Return per-call latencies (ms) for IngestionCommand.submit() bypass."""
    db = fresh_db(ws, "bypass.db")
    add_profile(db, PROFILE_ID)

    writer = build_immediate_admission_handler(db, profile_id=PROFILE_ID)
    repository = IngestionOperationRepository(db)

    def _noop_materialize(op):  # noqa: ANN001
        return []

    command = IngestionCommand(
        repository,
        write_queryable=writer,
        materialize=_noop_materialize,
    )

    # Warmup
    for i in range(n_warmup):
        command.submit(_bypass_request(-(i + 1)))

    # Measured calls
    latencies: list[float] = []
    for i in range(n_measure):
        req = _bypass_request(i)
        t0 = time.monotonic()
        command.submit(req)
        elapsed_ms = (time.monotonic() - t0) * 1_000
        latencies.append(elapsed_ms)

    db.close()
    return latencies


# ---------------------------------------------------------------------------
# Interleaved measurement — both paths under identical conditions
# ---------------------------------------------------------------------------

#: Fixed so the interleaving is reproducible run to run. Recorded in the result.
INTERLEAVE_SEED = 20260823


def measure_interleaved(
    ws: Path, n_warmup: int, n_measure: int, *, seed: int = INTERLEAVE_SEED,
) -> tuple[list[float], list[float]]:
    """Measure both paths in one interleaved pass. Returns (governed, bypass).

    WHY THIS REPLACED TWO SEQUENTIAL PHASES
    ---------------------------------------
    The governed path *contains* the bypass path: ``remember()`` ends in the
    same ``IngestionCommand.submit() -> write_queryable()`` the bypass path
    calls directly. So ``governed - bypass`` can only be non-negative, and any
    negative delta is a property of the measurement rather than of the code.

    Measuring them as two sequential phases produced exactly that. Each phase
    wrote a thousand rows to its own database before the next phase started, so
    whichever ran second inherited the first one's page-cache pressure and
    accumulated state. The minimums stayed within 0.1 ms of each other -- the
    fast path is identical once everything is cached -- while the medians drifted
    apart by 20 ms in the *wrong direction*.

    Interleaving removes the confound: both paths see the same cache state, the
    same thermal state, and the same point in the run. The order within each
    pair is drawn from a seeded shuffle of a balanced sequence, so neither path
    systematically occupies the favourable position, and the seed is recorded so
    the sequence is reproducible.
    """
    governed_db = fresh_db(ws, "governed.db")
    add_profile(governed_db, PROFILE_ID)
    governed_writer = build_immediate_admission_handler(
        governed_db, profile_id=PROFILE_ID,
    )
    runtime = CanonicalRememberRuntime(
        db=governed_db,
        profile_id=PROFILE_ID,
        writer=governed_writer,
        journal_path=ws / "governed_journal.db",
    )
    runtime.start()
    assert runtime.ready, "CanonicalRememberRuntime did not become ready"
    actor = _actor()

    # The ungoverned comparand is the SAME writer callback the governed path
    # ends in, so that the only difference between the two is the governance
    # envelope itself.
    #
    # It used to be ``IngestionCommand.submit()``, and that made the subtraction
    # invalid. ``build_immediate_admission_handler`` returns the bare
    # ``write_queryable`` callback, and ``IngestionCommand`` *wraps* that
    # callback with operation creation and a receipt transition -- three writes
    # in one transaction where the governed writer does one. So the two paths
    # were siblings, not nested: each carried bookkeeping the other did not, and
    # ``governed - bypass`` measured the difference between two pipelines rather
    # than the cost of governance. The sign of that difference is not even
    # stable -- it came out negative at the median on this store, which is
    # impossible for a genuine overhead.
    bypass_db = fresh_db(ws, "bypass.db")
    add_profile(bypass_db, PROFILE_ID)
    bypass_writer = build_immediate_admission_handler(
        bypass_db, profile_id=PROFILE_ID,
    )
    _bypass_op_seq = itertools.count(1)

    def _ungoverned_bare(request):  # noqa: ANN001, ANN202
        """The innermost writer, called with no caller-managed transaction."""
        return bypass_writer(request, f"bench-op-{next(_bypass_op_seq):07d}")

    def _noop_materialize(op):  # noqa: ANN001
        return []

    wrapped_command = IngestionCommand(
        IngestionOperationRepository(bypass_db),
        write_queryable=bypass_writer,
        materialize=_noop_materialize,
    )

    # Warm both before either is measured.
    for i in range(n_warmup):
        runtime.remember(_governed_request(-(i + 1)), actor)
        _ungoverned_bare(_bypass_request(-(i + 1)))
        wrapped_command.submit(_bypass_request(-(n_warmup + i + 1)))

    # A balanced sequence over all three arms, shuffled once with a recorded
    # seed so no arm systematically occupies the favourable position.
    order = (
        ["governed"] * n_measure
        + ["bare"] * n_measure
        + ["wrapped"] * n_measure
    )
    random.Random(seed).shuffle(order)

    governed: list[float] = []
    bare: list[float] = []
    wrapped: list[float] = []
    gi = bi = wi = 0
    for which in order:
        if which == "governed":
            req = _governed_request(gi); gi += 1
            t0 = time.monotonic()
            runtime.remember(req, actor)
            governed.append((time.monotonic() - t0) * 1_000)
        elif which == "bare":
            req = _bypass_request(bi); bi += 1
            t0 = time.monotonic()
            _ungoverned_bare(req)
            bare.append((time.monotonic() - t0) * 1_000)
        else:
            req = _bypass_request(1_000_000 + wi); wi += 1
            t0 = time.monotonic()
            wrapped_command.submit(req)
            wrapped.append((time.monotonic() - t0) * 1_000)

    runtime.stop()
    governed_db.close()
    bypass_db.close()
    return governed, bare, wrapped


# ---------------------------------------------------------------------------
# Percentile helpers
# ---------------------------------------------------------------------------


def _percentile(data: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation)."""
    if not data:
        raise ValueError("empty data")
    s = sorted(data)
    k = max(0, min(len(s) - 1, int(len(s) * pct / 100)))
    return s[k]


def _stats(latencies: list[float], label: str) -> dict:
    s = sorted(latencies)
    return {
        "label": label,
        "n": len(s),
        "p50_ms": round(_percentile(s, 50), 3),
        "p95_ms": round(_percentile(s, 95), 3),
        "p99_ms": round(_percentile(s, 99), 3),
        "mean_ms": round(statistics.mean(s), 3),
        "min_ms": round(s[0], 3),
        "max_ms": round(s[-1], 3),
        "stdev_ms": round(statistics.stdev(s), 3) if len(s) > 1 else 0.0,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    n_warmup: int = WARMUP_N,
    n_measure: int = MEASURE_N,
    out_dir: Path | None = None,
    n_trials: int | None = None,
) -> dict:
    """Run both paths and return a structured result dict.

    Unified contract: callers (run_all.py) may pass ``n_trials`` which maps
    to ``n_measure`` (governed latency measurement count).  Direct callers
    may still use ``n_measure`` / ``n_warmup``.  ``n_trials`` takes
    precedence when given.
    """
    if n_trials is not None:
        n_measure = int(n_trials)
        # Scale warmup proportionally for small trial counts so that
        # ``--trials 4`` remains fast; keep at least 1 warmup call.
        if n_measure < WARMUP_N:
            n_warmup = max(1, min(WARMUP_N, n_measure // 2 or 1))
    # Back-compat: some callers used out_dir as positional third arg; handle None

    with TempWorkspace() as ws:
        print(f"[exp_governed_latency] workspace: {ws}", flush=True)
        print(f"  superlocalmemory module: {_SLM_FILE}", flush=True)
        print(f"  warmup={n_warmup}, measure={n_measure}", flush=True)

        print("[exp_governed_latency] measuring both paths interleaved "
              f"(seed={INTERLEAVE_SEED}) ...", flush=True)
        governed_latencies, bare_latencies, wrapped_latencies = (
            measure_interleaved(ws, n_warmup, n_measure)
        )
        bypass_latencies = wrapped_latencies  # v1's comparand, kept comparable

    governed_stats = _stats(governed_latencies, "governed_write_envelope")
    bypass_stats = _stats(bypass_latencies, "ungoverned_wrapped_ingestion_command")
    bare_stats = _stats(bare_latencies, "ungoverned_bare_writer_no_transaction")

    delta_p50 = round(governed_stats["p50_ms"] - bypass_stats["p50_ms"], 3)
    delta_p99 = round(governed_stats["p99_ms"] - bypass_stats["p99_ms"], 3)
    bare_delta_p50 = round(governed_stats["p50_ms"] - bare_stats["p50_ms"], 3)
    bare_delta_p99 = round(governed_stats["p99_ms"] - bare_stats["p99_ms"], 3)
    # A governance envelope can only add work, so a valid overhead is >= 0.
    # Both candidate comparands yield a negative delta, which is a statement
    # about the comparison rather than about the code: neither is nested inside
    # the governed path. IngestionCommand does three writes in one transaction
    # where the governed writer does one; the bare callback is designed to run
    # inside a caller-managed transaction and pays an unbatched fsync per call
    # when it does not. Report the arms, and do not report a difference as an
    # overhead.
    overhead_well_defined = delta_p50 >= 0 and bare_delta_p50 >= 0

    method_para = (
        "Method B (in-process governed envelope). "
        "Entrypoint: CanonicalRememberRuntime.remember() — the same code path "
        "the HTTP /remember handler calls after request parsing. "
        "Governed chain includes: AdmissionJournal.prepare() "
        "(AES-256-GCM encrypt + fsync journal row), "
        "journal.mark_dispatched(), WriteCoordinator.submit() "
        "(queue → background worker thread wake), "
        "_handle_admission() → IngestionCommand.submit() → write_queryable() "
        "→ db.store_memory() + db.store_fact() (SQLite writer thread), "
        "_record_projection_obligations() (obligation ledger M033 INSERT), "
        "journal.mark_committed() (terminal journal update). "
        "Excluded: HTTP transport, request parsing, trust hook, daemon lifecycle. "
        f"Warmup: {n_warmup} calls (discarded). "
        f"Measurement: {n_measure} calls, each with unique idempotency_key "
        "(journal never short-circuits). "
        "Timing: time.monotonic() wall-clock per call. "
        "Bypass comparison: IngestionCommand.submit() directly on same throwaway DB, "
        "skipping journal/coordinator/obligations. "
        f"Machine: {platform.processor()} {platform.machine()}, "
        f"macOS {platform.mac_ver()[0]}, Python {sys.version.split()[0]}. "
        "DB files reside in APFS temporary directory (OS may use in-memory buffers "
        "after warmup — actual daemon runs on persistent APFS, so p50/p95/p99 "
        "may be higher in production due to fsync latency on first journal write)."
    )

    caveats = [
        (
            "WARMUP ADEQUACY: 30 warmup calls may leave APFS page-cache "
            "partially cold for very early measured calls. Mitigated by holding "
            "the same TempWorkspace open across warmup and measure phases."
        ),
        (
            "GIL CONTENTION: WriteCoordinator worker thread competes with the "
            "calling thread for the GIL on Python bookkeeping between SQLite I/O "
            "releases. This adds variance; p99 captures the tail accurately but "
            "absolute values are CPython-specific."
        ),
        (
            "BYPASS COMPARISON SCOPE: The bypass path (IngestionCommand.submit) "
            "includes the DB transaction and FTS indexing but omits the journal, "
            "coordinator queue/wake, obligation ledger, and manifest. The governance "
            "delta = governed p50 - bypass p50. A direct store_fast() comparison "
            "would also exclude operation-repository bookkeeping, making the true "
            "store_fast delta slightly smaller than shown."
        ),
    ]

    result = {
        "experiment": "exp_governed_latency",
        "slm_module_file": _SLM_FILE,
        "method": method_para,
        "governed": governed_stats,
        "bypass": bypass_stats,
        "ungoverned_bare": bare_stats,
        "governance_overhead_delta_p50_ms": delta_p50,
        "governance_overhead_delta_p99_ms": delta_p99,
        "bare_delta_p50_ms": bare_delta_p50,
        "bare_delta_p99_ms": bare_delta_p99,
        "overhead_well_defined": overhead_well_defined,
        "overhead_interpretation": (
            "A governance envelope can only add work, so a valid overhead is "
            ">= 0. Both candidate comparands give a negative p50 delta, which "
            "is a fact about the comparison and not about the code: neither is "
            "nested inside the governed path. IngestionCommand.submit performs "
            "operation creation, the queryable write and a receipt transition "
            "in one transaction, where the governed writer performs the "
            "queryable write alone. The bare callback is built to run inside a "
            "caller-managed transaction and pays an unbatched fsync per call "
            "when it does not. The three arms are reported; the difference "
            "between them is NOT a governance overhead and must not be quoted "
            "as one. Isolating that cost needs the same pipeline measured with "
            "the envelope enabled and disabled."
        ),
        "interleave_seed": INTERLEAVE_SEED,
        "caveats": caveats,
        "platform": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
    }

    print("\n=== GOVERNED WRITE-PATH LATENCY RESULTS ===")
    print(f"  Module: {_SLM_FILE}")
    for label, stats in [
        ("GOVERNED (journal + coordinator + writer)", governed_stats),
        ("UNGOVERNED, wrapped (IngestionCommand: 3 writes, 1 txn)", bypass_stats),
        ("UNGOVERNED, bare writer (no caller txn)", bare_stats),
    ]:
        print(f"\n  {label}")
        print(f"    n={stats['n']}  p50={stats['p50_ms']}ms  "
              f"p95={stats['p95_ms']}ms  p99={stats['p99_ms']}ms  "
              f"mean={stats['mean_ms']}ms  stdev={stats['stdev_ms']}ms")
    print(f"\n  governed - wrapped: p50={delta_p50}ms  p99={delta_p99}ms")
    print(f"  governed - bare:    p50={bare_delta_p50}ms  p99={bare_delta_p99}ms")
    if not overhead_well_defined:
        print("\n  !! Negative delta: neither comparand is nested inside the")
        print("     governed path, so no governance overhead is defined here.")
        print("     Report the arms; do not report a difference as an overhead.")

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "exp_governed_latency.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(f"\n  Results written to {out_dir / 'exp_governed_latency.json'}")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmup", type=int, default=WARMUP_N)
    parser.add_argument("--measure", type=int, default=MEASURE_N)
    # Unified contract flags: --trials maps to --measure for run_all.py
    parser.add_argument("--trials", type=int, default=None,
                        help="alias for --measure (unified run_all.py contract)")
    # Canonical output: --output-dir (DIR).  Keep --output (FILE) and
    # --output_dir underscore variant for backward compatibility.
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", type=Path, default=None,
                        help="output DIRECTORY (unified contract)")
    parser.add_argument("--output", type=Path, default=None,
                        help="output FILE path (legacy, deprecated: use --output-dir)")
    args = parser.parse_args()

    # Resolve trials alias
    n_measure = args.trials if args.trials is not None else args.measure
    output_dir = args.output_dir

    if args.output is not None and output_dir is None:
        # Legacy FILE mode: run then write single payload file
        result = run(n_warmup=args.warmup, n_measure=n_measure, out_dir=None)
        json_path = args.output.expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\n  Results written to {json_path} (legacy --output file)")
    elif output_dir is not None:
        run(n_warmup=args.warmup, n_measure=n_measure, out_dir=Path(output_dir))
    else:
        # No output flag: write to ./results for unified runner discovery
        run(n_warmup=args.warmup, n_measure=n_measure,
            out_dir=Path(__file__).parent / "results")
