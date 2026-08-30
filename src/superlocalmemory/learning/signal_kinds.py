# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Which ``learning_signals`` rows are feedback, and which are just exposure.

THE DISTINCTION, AND WHY IT DECIDES A RANKING PHASE
---------------------------------------------------
An **exposure** records that a fact was shown. A **feedback** signal records
that something happened afterwards. Only the second says anything about whether
the memory was any good, and the ranker's phase gate is meant to count the
second.

It counted both. Measured on a live ``learning.db``::

    candidate         5,350      exposure — one row per fact shown at recall
    legacy_feedback       2      the only real feedback in the table
                      ─────
                      5,352      what count_signals() returned

An inflation of 2,675x. Every surface that resolves a phase from that number
believed it had Phase 3 data — LightGBM active — on two feedback events, and
the model it activated had been trained on 972 rows whose labels were all 0.0.
So it reordered results at random and outranked the heuristic that would
otherwise have been used.

Correcting the count drops the system to Phase 1 (heuristic). That is not a
regression; it is the honest state, and the heuristic is the better ranker of
the two.

WHY A SHARED SET RATHER THAN A LITERAL AT EACH CALL SITE
--------------------------------------------------------
There are two ``count_signals()`` implementations — ``learning/database.py``
and the read-only view in ``core/recall_pipeline.py`` — and a phase computed
from one is compared against a threshold computed from the other. Two literals
is how they drift.

This module deliberately has no imports: ``_ReadOnlyLearningView`` exists to
read a learning DB *without* being able to initialise or mutate one, so it must
not pull the writer module in just to learn a set of strings.

EXCLUDING EXPOSURES RATHER THAN NAMING FEEDBACK. A new feedback kind (``dwell``,
``explicit``, ``cited``) must count the day it is introduced, without anyone
remembering to add it here — so the predicate is a NOT IN over the exposure
kinds, not an IN over the feedback kinds. The failure modes are not symmetric:
forgetting to add a feedback kind under-counts and quietly holds the ranker in
an earlier phase, while forgetting to add an exposure kind re-creates the 2,675x
inflation above.
"""

from __future__ import annotations

__all__ = ["EXPOSURE_SIGNAL_TYPES", "FEEDBACK_ONLY_SQL", "is_feedback"]

#: Signal kinds that record only that a fact was displayed.
#:
#: ``candidate`` is written once per fact per recall. ``shown`` is its sibling
#: in ``_fetch_training_rows``; there are no rows of it on a live store,
#: and it is listed here because the alternative is a predicate that counts it
#: as feedback the moment one appears.
EXPOSURE_SIGNAL_TYPES: frozenset[str] = frozenset({"candidate", "shown"})

#: AND-clause selecting feedback rows only. ``signal_type`` is ``NOT NULL`` in
#: the schema, so no COALESCE is needed — verified against the table definition
#: rather than assumed, because ``x != 'candidate'`` is NULL (and therefore
#: false) for a NULL x, which would silently drop rows.
FEEDBACK_ONLY_SQL: str = " AND signal_type NOT IN ({})".format(
    ", ".join(f"'{kind}'" for kind in sorted(EXPOSURE_SIGNAL_TYPES))
)


def is_feedback(signal_type: str | None) -> bool:
    """Whether ``signal_type`` counts toward the ranker's phase gate.

    The Python twin of :data:`FEEDBACK_ONLY_SQL`, for callers holding a row
    rather than building a query. A missing type counts as feedback: it is not
    one of the two known exposure writers, and under-counting is the failure
    mode that hides a phase transition.
    """
    return (signal_type or "") not in EXPOSURE_SIGNAL_TYPES
