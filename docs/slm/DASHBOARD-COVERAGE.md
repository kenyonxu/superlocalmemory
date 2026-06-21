# Dashboard Coverage — v3.4.21

This note tracks what the SLM dashboard does and does not surface, so
you can rely on the API when the UI is still catching up.

## Fully surfaced

- **Brain tab** (`/api/v3/brain`) — runtime health, consolidation state,
  consolidation cycle history, `action_outcomes` reward counters (when
  M006 has applied), pattern-miner output.
- **Skill Evolution tab** (`/api/v3/evolution`) — lineage, budget, and
  per-cycle LLM spend. See [skill-evolution.md](skill-evolution.md).
- **Cross-platform adapters** — install status per IDE.

## Stage 8 H-22 — deferred to the next release cycle

The following Stage 8 subsystems ship their data through the API in
v3.4.21 but do **not** have a dedicated dashboard tile yet. The data is
reachable via the REST endpoints listed below and via `slm status`.

| Subsystem | API endpoint | CLI | Dashboard tile |
|---|---|---|---|
| Reward model (`EngagementRewardModel`, LLD-08) | `GET /api/v3/brain` (`reward` block) | `slm status --json` | **Deferred** |
| Shadow test + online retrain (LLD-10) | `GET /api/v3/brain` (`shadow` block) | `slm status --json` | **Deferred** |
| Evolution cost log (LLD-11) | `GET /api/v3/evolution/costs` | `slm evolve --list` | **Deferred** |

### Why deferred

The Stage 8 harsh audit flagged dashboard visibility for the three
subsystems as a High. The backend work landed on schedule; the UI
Widgets are a separate design cycle (component + icons + chart) that
would delay v3.4.21 past the monthly release window Varun locked. The
data is not hidden — every number a tile would show is in the JSON
above, and the documentation in [skill-evolution.md](skill-evolution.md)
and [EVO-MEMORY.md](benchmarks/EVO-MEMORY.md) call it out.

### What you can do today

- Query the endpoints above directly — they return stable JSON.
- Run `slm status` for a human-readable summary.
- Open an issue if a tile you want is missing; we prioritise the next
  cycle's UI work by feedback.

### Planned for the next cycle

- Dedicated `Reward` tile on the Brain tab with a 7/30-day sparkline.
- `Shadow` tile with the rolling p-value and rollback counter.
- Evolution cost chart on the Skill Evolution tab (per-profile USD/day).

---

*Last updated: 2026-04-20 (v3.4.21 FINAL). Tracked in Stage 8
consolidated audit as H-22.*
