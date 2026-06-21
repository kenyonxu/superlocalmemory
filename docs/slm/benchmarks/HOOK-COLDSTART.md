# Hook Cold-Start Micro-Benchmark

Tracks Python import cold-start for the two Claude Code hook entry
points that sit on the user's hot path.

- **What's measured:** p95 wall-clock of five fresh-subprocess runs of
  `python -c "from <module> import main"` for
  `superlocalmemory.hooks.post_tool_outcome_hook` and
  `superlocalmemory.hooks.user_prompt_rehash_hook`. Driven by
  `tests/test_benchmarks/test_hook_cold_start.py`, skipped on Windows.
- **Current number (2026-04-20, MacBook Pro M-series, Python 3.14.3):**
  mean ≈ 50 ms, p95 ≈ 52-53 ms for both hooks — roughly an order of
  magnitude under budget.
- **Budget:** 500 ms p95, overridable per CI runner via the
  `SLM_HOOK_COLDSTART_BUDGET_MS` environment variable. The test fails
  loud with the full five-sample list if the budget is breached.
