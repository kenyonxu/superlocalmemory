"""SQL visibility for erasure intents whose canonical row still exists."""

from __future__ import annotations

import sqlite3


def has_tombstones(conn: sqlite3.Connection) -> bool:
    """Probe schema only; errors propagate rather than exposing erased content."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projection_tombstones'"
    ).fetchone() is not None


def erasure_clause(prefix: str = "", *, fact_column: str = "fact_id") -> str:
    """Correlate both owner and fact, including when the outer table is unaliased.

    Bare fact_id inside the subquery would resolve to pt.fact_id and accidentally
    hide every fact in a profile with any tombstone. Qualify the outer table.
    Tombstone membership is deliberately never cached: heal is immediately visible.
    """
    table = prefix or "atomic_facts"
    return (
        " AND NOT EXISTS (SELECT 1 FROM projection_tombstones pt"
        f" WHERE pt.profile_id = {table}.profile_id AND pt.fact_id = {table}.{fact_column})"
    )


def erasure_clause_for_connection(
    conn: sqlite3.Connection, prefix: str = "", *, fact_column: str = "fact_id",
) -> str:
    """Legacy schemas have no erasure intents; migrated schemas always enforce them."""
    return erasure_clause(prefix, fact_column=fact_column) if has_tombstones(conn) else ""


def memory_erasure_clause(conn: sqlite3.Connection, prefix: str = "memories") -> str:
    """Withhold source text when any child fact is being erased.

    Some tombstone writers predate memory_id capture. The retained canonical fact
    still identifies its source in that case; a captured memory_id also fences a
    source after its child row has already gone.
    """
    if not has_tombstones(conn):
        return ""
    return _memory_clause(prefix)


def _memory_clause(prefix: str) -> str:
    return (
        " AND NOT EXISTS (SELECT 1 FROM projection_tombstones pt"
        f" WHERE pt.profile_id = {prefix}.profile_id AND pt.memory_id = {prefix}.memory_id)"
        # Drive from the (small) tombstone table: CROSS JOIN fixes the join
        # order, so a large store never scans every fact once per memory.
        " AND NOT EXISTS (SELECT 1 FROM projection_tombstones pt"
        " CROSS JOIN atomic_facts erasing ON erasing.profile_id = pt.profile_id"
        " AND erasing.fact_id = pt.fact_id"
        f" WHERE pt.profile_id = {prefix}.profile_id"
        f" AND erasing.memory_id = {prefix}.memory_id)"
    )


def aggregate_erasure_clause(
    conn: sqlite3.Connection, prefix: str, *, column: str = "fact_ids_json",
) -> str:
    """Withhold a precomputed summary if any of its source members is erasing.

    ``column`` names the JSON list of source fact ids (``source_fact_ids`` on
    ``consolidated_summaries`` and ``core_memory_blocks``).
    """
    if not has_tombstones(conn):
        return ""
    return _aggregate_clause(prefix, column)


def _aggregate_clause(prefix: str, column: str = "fact_ids_json") -> str:
    return (
        " AND NOT EXISTS (SELECT 1 FROM json_each("
        f"{prefix}.{column}) member JOIN projection_tombstones pt"
        f" ON pt.profile_id = {prefix}.profile_id AND pt.fact_id = member.value)"
    )


def aggregate_erasure_clause_for_db(db, prefix: str, *, column: str = "fact_ids_json") -> str:
    """Use the manager's cached schema probe, with execute-only legacy support."""
    probe = getattr(type(db), "_has_projection_tombstones_table", None)
    if callable(probe):
        present = probe(db)
    else:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='projection_tombstones'")
        present = any(dict(r).get("name") == "projection_tombstones" for r in rows)
    return _aggregate_clause(prefix, column) if present else ""


def erasure_cache_ready(db) -> bool:
    """A visibility cache needs erasure changes in its normal currency log."""
    rows = db.execute("SELECT name FROM sqlite_master WHERE name IN "
                      "('projection_tombstones','trg_erasure_visibility_insert',"
                      "'trg_erasure_visibility_delete','trg_erasure_visibility_update')")
    names = {dict(r).get("name") for r in rows}
    return "projection_tombstones" not in names or len(names) == 4


def erasure_clause_for_db(db) -> str:
    """The fact predicate for execute-only read wrappers, with no fail-open probe."""
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                      "AND name='projection_tombstones'")
    return erasure_clause() if any(
        dict(r).get("name") == "projection_tombstones" for r in rows
    ) else ""


def erasure_endpoint_clause(conn: sqlite3.Connection, edge: str, column: str, fact: str) -> str:
    """Use the canonical owner; a deleted endpoint still has its tombstone.

    Fact IDs are globally unique. When the canonical row is gone, any surviving
    tombstone for that ID fences the endpoint, including a cross-profile edge.
    """
    if not has_tombstones(conn):
        return ""
    return (
        " AND NOT EXISTS (SELECT 1 FROM projection_tombstones pt"
        f" WHERE pt.fact_id = {edge}.{column}"
        f" AND ({fact}.profile_id IS NULL OR pt.profile_id = {fact}.profile_id))"
    )


def memory_erasure_clause_for_db(db, prefix: str = "memories") -> str:
    """The source predicate through read-only SQL, without taking the writer lock."""
    probe = getattr(type(db), "_has_projection_tombstones_table", None)
    if callable(probe):
        present = probe(db)
    else:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='projection_tombstones'")
        present = any(dict(r).get("name") == "projection_tombstones" for r in rows)
    return _memory_clause(prefix) if present else ""
