# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Control-flow signals for best-effort materialization work.

These exceptions deliberately live outside the ingestion state machine and
embedding implementation so either layer can request a durable deferral
without introducing an import cycle.
"""

from __future__ import annotations


class MaterializationDeferred(RuntimeError):
    """Best-effort enrichment yielded to a runtime transition.

    The queryable projection is already durable.  This signal must be handled
    by :class:`IngestionCommand` as a requeue, never as a failed attempt.
    """
