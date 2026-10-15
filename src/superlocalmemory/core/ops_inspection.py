# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Reading the failure surfaces honestly, for the workspaces a caller may see.

Split out of ``ops_remediation`` in 4.1.25 for two audit findings:

* **OPS-10: an unreadable store is not a clean one.** Every read here used to
  catch every error and return an empty list, so a corrupt, locked or
  unreadable ``memory.db`` reported "0 failures": the same answer as a healthy
  queue. Now each category says ``ok``, ``not_applicable`` (its table was never
  created, an older store) or ``unavailable``, and the result carries
  ``success: False`` when any category could not be read. Callers turn that
  into a 503, an MCP error or a non-zero exit, never into "all clear".
* **OPS-22: workspace administration is scoped.** :func:`operation_profiles`
  finds the workspaces an operation belongs to, from every table that records
  one, so a route can require MANAGE on the operation's own workspace instead
  of on whichever workspace happens to be active; ``allowed_profiles`` limits a
  listing to the workspaces the caller manages.

Error text in results names the kind of failure only (an exception class),
never paths or memory content.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: Maximum automatic materialization/apply attempts (mirrors ingestion_command).
MAX_ATTEMPTS = 10

# A failed obligation is exhausted when its apply/erase attempts ran out, or --
# for an erasure, which is closed by proof rather than by retrying -- when it
# has been re-proven and found unconfirmed that many times. Two ? = MAX_ATTEMPTS.
EXHAUSTED_PREDICATE = "(attempts >= ? OR (kind = 'erase' AND verify_attempts >= ?))"

_SYNC_EXHAUSTED = (
    "Background sync failed after maximum retries — use Force Re-sync or Cancel."
)
_ERASE_UNCONFIRMED = (
    "A deletion could not be confirmed: the memory or one of its search "
    "indexes still holds it. Delete the memory again, then use Reconcile; "
    "or Cancel to dismiss."
)
_UNAVAILABLE_MESSAGE = (
    "The list of failed operations could not be read, so it is unknown whether "
    "any saves failed. Run 'slm doctor' and check that memory.db is readable."
)

OK = "ok"
NOT_APPLICABLE = "not_applicable"
UNAVAILABLE = "unavailable"

#: (result key, status key, table it reads).
_CATEGORIES = (
    ("dead_letter", "dead_letter", "dead_letter_operations"),
    ("degraded_manifests", "degraded_manifests", "completion_manifests"),
    ("exhausted_obligations", "exhausted_obligations", "projection_obligations"),
)


def _missing_table(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc).lower()


def _connect(db_path: Path) -> sqlite3.Connection:
    """A connection to an existing store; a missing file is an error, never a new store."""
    if not db_path.is_file():
        raise FileNotFoundError("memory.db is missing")
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _profile_clause(column: str, profiles: Iterable[str] | None) -> tuple[str, list[Any]]:
    if profiles is None:
        return "", []
    names = sorted(set(profiles))
    if not names:
        return f" AND {column} IN (NULL)", []  # nothing visible
    return f" AND {column} IN ({','.join('?' * len(names))})", names


#: A dead-letter row whose operation later completed is a record of an old failure,
#: not a failed save. Readers skip it; ``slm ops repair-saves`` removes it.
NOT_STALE = (
    "NOT EXISTS (SELECT 1 FROM ingestion_operations o "
    "WHERE o.operation_id = dead_letter_operations.original_op_id AND o.state = 'complete')"
)


def _dead_letter_rows(conn, profiles) -> list[dict]:
    clause, params = _profile_clause("profile_id", profiles)
    sql = (
        "SELECT original_op_id, error, attempt_count, profile_id, dead_lettered_at "
        "FROM dead_letter_operations WHERE {live}1=1" + clause +
        " ORDER BY dead_lettered_at DESC LIMIT 200"
    )
    try:
        rows = conn.execute(sql.format(live=NOT_STALE + " AND "), params).fetchall()
    except sqlite3.OperationalError as exc:
        if not _missing_table(exc):
            raise
        # A store without ingestion_operations has no completed operations to skip.
        rows = conn.execute(sql.format(live=""), params).fetchall()
    return [{
        "category": "dead_letter",
        "operation_id": row["original_op_id"],
        "error": row["error"] or "",
        "attempts": row["attempt_count"] or 0,
        "profile_id": row["profile_id"] or "",
        "when": row["dead_lettered_at"],
        "what_happened": "Ingestion failed after maximum retries — needs manual action.",
    } for row in rows]


def _degraded_rows(conn, profiles) -> list[dict]:
    clause, params = _profile_clause("profile_id", profiles)
    rows = conn.execute(
        "SELECT operation_id, profile_id, state, updated_at FROM completion_manifests "
        f"WHERE state = 'DEGRADED'{clause} ORDER BY updated_at DESC LIMIT 200", params,
    ).fetchall()
    return [{
        "category": "degraded_manifest",
        "operation_id": row["operation_id"],
        "state": row["state"],
        "profile_id": row["profile_id"] or "",
        "when": row["updated_at"],
        "what_happened": "Operation completed partially — some projections did not apply.",
    } for row in rows]


def _detail_error(detail_raw: str | None) -> str:
    """The plain-English reason recorded on an obligation, or ``""``."""
    try:
        parsed = json.loads(detail_raw) if detail_raw else {}
    except (TypeError, ValueError):
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    return str(error) if error else ""


def _exhausted_rows(conn, profiles) -> list[dict]:
    """FAILED obligations that ran out of attempts, not admin-cancelled."""
    clause, params = _profile_clause("profile_id", profiles)
    rows = conn.execute(
        "SELECT operation_id, MAX(profile_id) AS profile_id, "
        "MAX(attempts) AS attempts, MAX(updated_at) AS updated_at, "
        "GROUP_CONCAT(DISTINCT kind) AS kinds, "
        "MAX(CASE WHEN kind = 'erase' THEN detail END) AS erase_detail, "
        "SUM(CASE WHEN detail LIKE '%admin_cancel%' THEN 1 ELSE 0 END) AS cancelled "
        f"FROM projection_obligations WHERE state = 'failed' AND {EXHAUSTED_PREDICATE}"
        f"{clause} GROUP BY operation_id ORDER BY updated_at DESC LIMIT 200",
        [MAX_ATTEMPTS, MAX_ATTEMPTS, *params],
    ).fetchall()
    result: list[dict] = []
    for row in rows:
        if row["cancelled"]:
            continue
        kinds = set((row["kinds"] or "").split(","))
        erase_only = kinds == {"erase"}
        entry = {
            "category": "exhausted_obligation",
            "operation_id": row["operation_id"],
            "kind": "erase" if erase_only else ",".join(sorted(kinds)),
            "attempts": row["attempts"] or 0,
            "profile_id": row["profile_id"] or "",
            "when": row["updated_at"],
            "what_happened": _ERASE_UNCONFIRMED if erase_only else _SYNC_EXHAUSTED,
        }
        reason = _detail_error(row["erase_detail"]) if erase_only else ""
        if reason:
            entry["error"] = reason
        result.append(entry)
    return result


_READERS = {
    "dead_letter": _dead_letter_rows,
    "degraded_manifests": _degraded_rows,
    "exhausted_obligations": _exhausted_rows,
}


def _read_category(conn, key: str, profiles) -> tuple[list[dict], dict]:
    try:
        return _READERS[key](conn, profiles), {"status": OK}
    except Exception as exc:  # noqa: BLE001 - classified, logged and reported
        if _missing_table(exc):
            return [], {"status": NOT_APPLICABLE}
        logger.error("ops inspection: %s could not be read: %s", key, exc)
        return [], {"status": UNAVAILABLE, "error": type(exc).__name__}


def list_failed_operations(
    db_path: str | Path,
    profile_id: str | None = None,
    *,
    allowed_profiles: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Every failure surface, with whether each one could actually be read.

    ``profile_id`` narrows to one workspace; ``allowed_profiles`` (``None`` =
    no limit) is the set of workspaces the caller may see. ``success`` is False
    when any category is ``unavailable``; ``total`` then counts only what was
    read and must not be shown as "no failures".
    """
    profiles = None if allowed_profiles is None else set(allowed_profiles)
    if profile_id is not None:
        profiles = {profile_id} if profiles is None else profiles & {profile_id}
    result: dict[str, Any] = {key: [] for key, _, _ in _CATEGORIES}
    inspection: dict[str, dict] = {}
    try:
        conn = _connect(Path(db_path))
    except Exception as exc:  # noqa: BLE001 - reported, never "zero failures"
        logger.error("ops inspection: memory.db could not be opened: %s", exc)
        inspection = {key: {"status": UNAVAILABLE, "error": type(exc).__name__}
                      for key, _, _ in _CATEGORIES}
    else:
        try:
            for key, _, _ in _CATEGORIES:
                result[key], inspection[key] = _read_category(conn, key, profiles)
        finally:
            conn.close()
    unavailable = sorted(k for k, v in inspection.items() if v["status"] == UNAVAILABLE)
    result["total"] = sum(len(result[key]) for key, _, _ in _CATEGORIES)
    result["inspection"] = inspection
    result["success"] = not unavailable
    if unavailable:
        result["unavailable"] = unavailable
        result["error"] = _UNAVAILABLE_MESSAGE
    return result


def get_failure_counts(db_path: str | Path) -> dict[str, Any]:
    """Counts for /status and ``slm doctor``, plus ``unavailable`` categories.

    A count that could not be read is listed in ``unavailable`` (and left at 0
    only so old readers keep working); readers must check that list before
    saying "no failures".
    """
    counts: dict[str, Any] = {
        "dead_letter_count": 0, "degraded_operations": 0, "exhausted_obligations": 0,
    }
    queries = (
        ("dead_letter_count",
         f"SELECT COUNT(*) AS c FROM dead_letter_operations WHERE {NOT_STALE}", ()),
        ("degraded_operations",
         "SELECT COUNT(*) AS c FROM completion_manifests WHERE state = 'DEGRADED'", ()),
        ("exhausted_obligations",
         "SELECT COUNT(DISTINCT operation_id) AS c FROM projection_obligations "
         f"WHERE state = 'failed' AND {EXHAUSTED_PREDICATE} "
         "AND (detail IS NULL OR detail NOT LIKE '%admin_cancel%')",
         (MAX_ATTEMPTS, MAX_ATTEMPTS)),
    )
    unavailable: list[str] = []
    try:
        conn = _connect(Path(db_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_failure_counts: memory.db could not be opened: %s", exc)
        counts["unavailable"] = [key for key, _, _ in queries]
        return counts
    try:
        for key, sql, params in queries:
            try:
                row = conn.execute(sql, params).fetchone()
                counts[key] = int(row["c"]) if row else 0
            except Exception as exc:  # noqa: BLE001 - classified below
                if key == "dead_letter_count" and _missing_table(exc):
                    # No ingestion_operations to say which saves completed: count them all.
                    try:
                        counts[key] = int(conn.execute(
                            "SELECT COUNT(*) AS c FROM dead_letter_operations").fetchone()["c"])
                        continue
                    except Exception as inner:  # noqa: BLE001
                        exc = inner
                if not _missing_table(exc):
                    logger.warning("get_failure_counts: %s unreadable: %s", key, exc)
                    unavailable.append(key)
    finally:
        conn.close()
    counts["unavailable"] = unavailable
    return counts


#: Tables that record which workspace an operation belongs to.
_OWNER_TABLES = (
    ("dead_letter_operations", "original_op_id"),
    ("ingestion_operations", "operation_id"),
    ("completion_manifests", "operation_id"),
    ("projection_obligations", "operation_id"),
)


def operation_profiles(db_path: str | Path, operation_id: str) -> set[str]:
    """Every workspace that holds a record of ``operation_id``.

    Raises when the store cannot be read, so a caller cannot mistake "unknown"
    for "belongs to nobody". An empty set means no failure surface knows it.
    """
    found: set[str] = set()
    conn = _connect(Path(db_path))
    try:
        for table, column in _OWNER_TABLES:
            try:
                rows = conn.execute(
                    f"SELECT DISTINCT profile_id FROM {table} WHERE {column} = ?",
                    (operation_id,),
                ).fetchall()
            except Exception as exc:  # noqa: BLE001
                if _missing_table(exc):
                    continue
                raise
            found.update(str(row["profile_id"] or "") for row in rows)
    finally:
        conn.close()
    return found


__all__ = [
    "EXHAUSTED_PREDICATE",
    "MAX_ATTEMPTS",
    "NOT_APPLICABLE",
    "OK",
    "UNAVAILABLE",
    "get_failure_counts",
    "list_failed_operations",
    "operation_profiles",
]
