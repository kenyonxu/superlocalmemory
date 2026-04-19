# Evo-Memory Benchmark

**Status:** _pending first run_ (NUMBERS PENDING — Track A.3 online retrain must ship before a legitimate ≥10% lift reading is possible).

**Publish gate:** See § Publish Gate below. The harness emits a tier
automatically — this document is rewritten from the template every run.

---

## What this is

Evo-Memory is a 30-day synthetic-profile benchmark that measures the
day-1 vs day-N recall quality of SuperLocalMemory's learning loop. The
hypothesis: a system that actually learns should rank previously-useful
facts higher on day-30 than it did on day-1 for the same test queries.

- **Fixture:** `tests/test_benchmarks/fixtures/evo_memory_synthetic_v1.jsonl`
  (500 seeds across 10 topics, 30 days of interleaved activity and test
  queries, seed `12345`). Pinned by SHA-256 in the sidecar file.
- **Harness:** `tests/test_benchmarks/evo_memory.py` (`EvoMemoryBenchmark`).
- **Runner:** `tests/test_benchmarks/test_evo_memory_runner.py`, gated
  by `@pytest.mark.benchmark` — excluded from the default regression,
  opt-in via `pytest tests/test_benchmarks/ -m benchmark` or
  `slm benchmark`.

The harness never touches `~/.superlocalmemory`. It refuses any
`profile_id` other than `bench_v1`, and any `data_dir` that contains
`.superlocalmemory` in its path. All writes go to an isolated
`bench_memory.db` in a pytest `tmp_path`.

## Methodology

- **Seeds.** Day 0 loads 500 synthetic facts (50 per topic) into the
  isolated memory DB and the ranker's retrieval-prior table.
- **Daily activity.** Each simulated day emits 3-5 activity queries
  with pre-computed reward labels (70% `cite`, 20% `requery`, 10%
  `edit`) — drives the real `EngagementRewardModel.finalize_outcome`
  path, which writes to `action_outcomes`.
- **Prior nudge.** Positive rewards nudge the relevant facts' retrieval
  prior up (`+0.05`); negative rewards nudge down (`-0.10`). Clamped to
  `[-1, +1]`. This stands in for the LightGBM retrain (Track A.3);
  once the real trainer ships, the fixture should yield a stronger
  lift via the same ground-truth outcome rows.
- **Test queries.** Each day includes 50 held-out queries with stable
  `relevant_seed_idxs`. MRR@10 / Recall@10 / p95 latency are measured
  on these only — never on activity queries. Test queries do not write
  rewards and do not nudge priors (LLD-14 §4.4 source 1).
- **Measured days:** 1, 7, 14, 30. Day 30 vs day 1 is the gate.

### Relevance

Ground-truth: a result is relevant iff its `fact_id` matches one of
the query's `relevant_seed_idxs`. This is fixture-defined, reproducible
across runs. The reward channel (LLD-08) is exercised for side effect,
not gated on.

## Reproducibility

```bash
git clone https://github.com/varun369/superlocalmemory.git
cd superlocalmemory
pip install -e .
PYTHONPATH=src pytest tests/test_benchmarks/ -m benchmark
```

Fixture SHA-256 is pinned (see
`tests/test_benchmarks/fixtures/evo_memory_synthetic_v1.sha256`). The
harness verifies the hash before reading any data; a mismatch fails
loud with `ValueError`. Two runs on the same machine produce
byte-identical MRR / Recall outputs (p95 latency, which is wall-clock
derived by design, is the sole clock-normalised field).

## Results

_NUMBERS PENDING._ Run `slm benchmark` (or the pytest invocation
above) to populate this section. The harness writes
`docs/benchmarks/evo_memory_results_v1.json` with the full shape
from LLD-14 §5.3 and regenerates four SVG charts in
`docs/benchmarks/charts/`:

- `mrr_by_day.svg`
- `latency_by_day.svg`
- `phase_transitions.svg`
- `consolidation_impact.svg`

## Honesty Clause

This is a synthetic benchmark. It exercises our learning loop on data
we designed to exercise it. It is not a claim about real-user workloads.
Real-user outcomes are measured via the opt-in engagement reward model
(LLD-08) and aggregated non-publicly. The synthetic fork of Google's
Evo-Memory (arXiv:2511.20857) uses our own fixture, our own topic
distribution, and our own queries — we keep the shape, not the data.

## Publish Gate

| Lift (Day-30 vs Day-1 MRR@10) | Status                     | Exit | Linked from |
|-------------------------------|----------------------------|------|-------------|
| ≥ 10 %                        | `stable`                   | 0    | README + site |
| 5 % – 10 %                    | `draft`                    | 0    | docs index only |
| < 5 %                         | `regression-investigation` | 1    | triage branch only |

The runner evaluates the gate automatically. CI fails on
`regression-investigation` and requires a human reviewer before
merging to `main`.

## Known Limitations

1. Mode A only. Mode B/C (local LLM pipelines) will be added in
   v3.4.23 once latency is re-budgeted.
2. The current prior-nudge is a stand-in for Track A.3's LightGBM
   retrain. When the full retrainer ships, expected lift grows; the
   current floor is an honest reflection of what the reward channel
   alone can achieve.
3. The harness is single-threaded and single-process so the 5-minute
   wall-time budget is deterministic across CI runners.

---

_Template version: 1.0 (LLD-14 §7)._
