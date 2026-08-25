#!/usr/bin/env python3
"""exp13 — What does the governance envelope cost? Measured, not subtracted.

The previous version of the paper quoted a governance overhead derived by
subtracting one write pipeline from another. That number was withdrawn: the two
pipelines are not comparable, and two successive explanations of *why* were both
wrong on inspection. This experiment abandons subtraction.

Instead it times the envelope's components DIRECTLY, inside the real governed
write, on the same connection, under the same transaction and the same pragmas:

    journal.prepare()          AES-256-GCM encrypt + durable journal row
    journal.mark_dispatched()  advisory journal transition
    admitted_epoch()           the generation fence
    _record_projection_obligations()  obligation-ledger insert
    journal.mark_committed()   terminal journal row

Nothing is bypassed and no comparand is constructed, so there is no
comparability question to get wrong. The reported cost is the sum of the
measured components as a fraction of the measured whole.

SAFETY: throwaway databases only. Never touches ~/.superlocalmemory or port 8765.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _harness import TempWorkspace, add_profile, fresh_db  # noqa: E402

from superlocalmemory.core import remember_runtime as rr  # noqa: E402
from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime  # noqa: E402
from superlocalmemory.storage import admission_journal as aj  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from exp_governed_latency import (  # noqa: E402
    ACTOR_ID, PROFILE_ID, _actor, _governed_request,
    build_immediate_admission_handler,
)

N_WARMUP = 30
N_MEASURE = 400

_acc: dict[str, list[float]] = {}


def _timed(component: str, fn):
    def wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            _acc.setdefault(component, []).append(
                (time.perf_counter() - t0) * 1_000)
    return wrapper


def main() -> int:
    with TempWorkspace() as ws:
        db = fresh_db(ws, "envelope.db")
        add_profile(db, PROFILE_ID)
        writer = build_immediate_admission_handler(db, profile_id=PROFILE_ID)

        # Instrument the envelope components in place. The governed path is
        # otherwise untouched: same code, same order, same transaction.
        orig_prepare = aj.AdmissionJournal.prepare
        orig_dispatch = aj.AdmissionJournal.mark_dispatched
        orig_commit = aj.AdmissionJournal.mark_committed
        orig_epoch = rr.admitted_epoch
        orig_oblig = CanonicalRememberRuntime._record_projection_obligations

        aj.AdmissionJournal.prepare = _timed("journal_prepare", orig_prepare)
        aj.AdmissionJournal.mark_dispatched = _timed("journal_dispatch", orig_dispatch)
        aj.AdmissionJournal.mark_committed = _timed("journal_commit", orig_commit)
        rr.admitted_epoch = _timed("generation_fence", orig_epoch)
        CanonicalRememberRuntime._record_projection_obligations = _timed(
            "projection_obligations", orig_oblig)

        try:
            runtime = CanonicalRememberRuntime(
                db=db, profile_id=PROFILE_ID, writer=writer,
                journal_path=ws / "envelope_journal.db",
            )
            runtime.start()
            assert runtime.ready, "runtime did not become ready"
            actor = _actor()

            for i in range(N_WARMUP):
                runtime.remember(_governed_request(-(i + 1)), actor)
            _acc.clear()   # discard warmup

            totals: list[float] = []
            for i in range(N_MEASURE):
                t0 = time.perf_counter()
                runtime.remember(_governed_request(i), actor)
                totals.append((time.perf_counter() - t0) * 1_000)
            runtime.stop()
        finally:
            aj.AdmissionJournal.prepare = orig_prepare
            aj.AdmissionJournal.mark_dispatched = orig_dispatch
            aj.AdmissionJournal.mark_committed = orig_commit
            rr.admitted_epoch = orig_epoch
            CanonicalRememberRuntime._record_projection_obligations = orig_oblig
            db.close()

    def pct(xs):
        xs = sorted(xs)
        return {
            "n": len(xs),
            "p50_ms": round(statistics.median(xs), 4),
            "p99_ms": round(xs[min(len(xs) - 1, int(len(xs) * 0.99))], 4),
            "mean_ms": round(statistics.fmean(xs), 4),
        }

    comps = {k: pct(v) for k, v in sorted(_acc.items())}
    envelope_p50 = round(sum(c["p50_ms"] for c in comps.values()), 4)
    whole = pct(totals)
    out = {
        "experiment": "exp13_governance_envelope_cost",
        "method": "direct component timing inside the real governed write; "
                  "no comparand pipeline is constructed and no subtraction is performed",
        "governed_write_total": whole,
        "envelope_components": comps,
        "envelope_p50_ms": envelope_p50,
        "envelope_share_of_p50": round(envelope_p50 / whole["p50_ms"], 4),
        "caveats": [
            "In-process. HTTP transport, request parsing and the trust hook are excluded.",
            "Temporary filesystem; a persistent disk raises the journal fsync component.",
            "Component timings include the timing wrapper itself (sub-microsecond).",
            "Sum of component p50s is not the p50 of the sum; reported as an indicative share.",
        ],
    }
    print(json.dumps(out, indent=2))
    res = Path(__file__).resolve().parent / "results"
    res.mkdir(exist_ok=True)
    (res / "exp13_governance_envelope_cost.json").write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
