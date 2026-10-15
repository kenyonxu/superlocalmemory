# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A save's original projection cannot be replaced by a derived sibling.

The write pipeline bypasses consolidation for protected source facts. This
compatibility guard also refuses NOOP replacement of a promoted source,
including replacement by a fact created by the same save.
"""

from __future__ import annotations

from collections.abc import Collection


def keeps_own_fact(action: object, is_queryable_promotion: bool,
                   created_ids: Collection[str]) -> bool:
    """True when ``action`` would delete this memory's own fact for another's."""
    kind = getattr(getattr(action, "action_type", None), "value", None)
    target = getattr(action, "existing_fact_id", "") or ""
    return kind == "noop" and is_queryable_promotion and bool(target)
