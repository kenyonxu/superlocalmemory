# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com | https://varunpratap.com

"""Checks that ask whether a mechanism is *effective*, not merely present.

Three questions get confused in a system this size:

* **Implemented** — the code exists. A grep answers this.
* **Reachable** — something calls it. A call-graph trace answers this.
* **Effective** — it has actually changed an outcome against real data.
  **Neither of the above answers this.** Only querying the store does.

A mechanism can pass the first two questions for months and fail the third
silently: a learner whose reward channel emits a constant still records plays,
and a conditional path guarded by a missing column still appears in coverage.
Nothing raises, nothing logs, and every file is present.

The two checks here answer the third question directly.

* :mod:`.prior_distance` — has a Bayesian learner's posterior actually moved
  away from its prior?
* :mod:`.join_liveness` — has a schema-guarded code path ever executed against
  this store, and if not, which requirement is missing?

Both are read-only, both are fail-soft, and neither is on a hot path.
"""

from superlocalmemory.reliability.join_liveness import (
    GuardVerdict,
    check_schema_guards,
)
from superlocalmemory.reliability.prior_distance import (
    DEFAULT_MIN_OBSERVATIONS,
    LearnerVerdict,
    check_beta_learners,
)

__all__ = [
    "DEFAULT_MIN_OBSERVATIONS",
    "GuardVerdict",
    "LearnerVerdict",
    "check_beta_learners",
    "check_schema_guards",
]
