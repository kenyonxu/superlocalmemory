# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Event-time start, shared by save and automatic re-enrichment."""

from __future__ import annotations


def valid_from(fact) -> str | None:
    """When the fact became true: the event date, else the observation date.

    Never ``created_at``: save time is transaction time, recorded separately.
    Using it as an event start made every undated fact "not yet valid" for an
    ``as_of`` before it was saved, and made a later ``valid_until`` earlier
    than its own start. Unknown stays unknown (None).
    """
    return fact.referenced_date or fact.observation_date or None


def mirror(db, fact_id: str, profile_id: str, start: str | None) -> None:
    """Keep M013's fact column in step with the canonical temporal record."""
    if start is None:
        return
    present = getattr(db, '_fact_valid_from_column', False)
    if not present:
        present = any(r['name'] == 'valid_from'
                      for r in db.execute('PRAGMA table_info(atomic_facts)'))
        if present:  # cached once seen; re-probed while absent (a later migration)
            try:
                db._fact_valid_from_column = True
            except AttributeError:
                pass
    if present:
        db.execute('UPDATE atomic_facts SET valid_from=? WHERE fact_id=? AND profile_id=?',
                   (start, fact_id, profile_id))
