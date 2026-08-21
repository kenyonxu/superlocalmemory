# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Fail-closed authorization helpers for retrieval candidate paths.

Candidate generators may use caches, approximate indexes, or graph stores that
are not the authorization source of truth.  Every such path must therefore
re-authorize fact IDs through ``DatabaseManager.get_facts_by_ids()``, whose SQL
is built by the canonical ``_scope_where`` predicate.
"""

from __future__ import annotations

from typing import Any, Iterable

from superlocalmemory.storage.database import _scope_where


def authorized_fact_ids(
    db: Any,
    fact_ids: Iterable[str],
    profile_id: str,
    *,
    include_global: bool = False,
    include_shared: bool = False,
) -> set[str]:
    """Return only IDs visible under the canonical scope predicate.

    Authorization errors fail closed.  The stable de-duplication avoids SQLite
    parameter waste without changing candidate order at the caller boundary.
    """
    unique_ids = list(dict.fromkeys(fact_ids))
    if not unique_ids:
        return set()
    try:
        facts = db.get_facts_by_ids(
            unique_ids,
            profile_id,
            include_global=bool(include_global),
            include_shared=bool(include_shared),
        )
        if isinstance(facts, list):
            return {fact.fact_id for fact in facts}
    except Exception:
        pass

    # Lightweight DB wrappers used by maintenance paths may expose execute()
    # without the higher-level method.  Keep the same canonical predicate.
    try:
        where, params = _scope_where(
            profile_id,
            include_global=include_global,
            include_shared=include_shared,
        )
        placeholders = ",".join("?" for _ in unique_ids)
        # Mirror the primary path's visibility rule, not just its scope rule.
        # This branch exists for lightweight wrappers that expose execute() but
        # not get_facts_by_ids, and it was authorizing withheld and
        # soft-deleted rows that the primary path refuses -- so any channel
        # whose db object took this branch had a different idea of what is
        # visible than the engine that hydrates its results.
        # Resolved on the TYPE, not the instance. A MagicMock fabricates any
        # attribute you ask for, so an instance check returns a callable that
        # returns another MagicMock, whose repr then lands in the SQL string and
        # makes the whole query a syntax error -- and this function's `except`
        # turns that into an empty authorized set, i.e. every candidate silently
        # dropped. tests/test_retrieval/test_spreading_activation.py caught it
        # by passing exactly such a mock. The same reasoning is already written
        # up in retrieval/engine.py for the reranker's optional contract.
        visible = ""
        clause_fn = getattr(type(db), "visible_fact_clause", None)
        if callable(clause_fn):
            try:
                visible = clause_fn(db)
            except Exception:  # noqa: BLE001 -- fall back to scope-only
                visible = ""
        rows = db.execute(
            f"SELECT fact_id FROM atomic_facts WHERE fact_id IN ({placeholders}) "
            f"AND {where}{visible}",
            (*unique_ids, *params),
        )
        if not isinstance(rows, list):
            rows = list(rows)
        return {dict(row)["fact_id"] for row in rows}
    except Exception:
        return set()


def filter_authorized_results(
    db: Any,
    results: Iterable[tuple[str, float]],
    profile_id: str,
    *,
    include_global: bool = False,
    include_shared: bool = False,
) -> list[tuple[str, float]]:
    """Preserve result order/scores while removing unauthorized fact IDs."""
    materialized = list(results)
    allowed = authorized_fact_ids(
        db,
        (fact_id for fact_id, _score in materialized),
        profile_id,
        include_global=include_global,
        include_shared=include_shared,
    )
    return [item for item in materialized if item[0] in allowed]
