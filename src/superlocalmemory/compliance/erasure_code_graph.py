# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Erasing a profile's share of ``code_graph.db`` (GDPR Art. 17).

The graph carries repository paths, file names and symbol names, and no table in
it has a profile_id. When the store holds ONE profile the whole graph belongs to
that person and wiping it is exactly right.

When it holds more than one, wiping it destroys the other people's data too, and
an erasure request that erases a second data subject is itself a breach, not an
over-achievement. So in that case only the rows that are unambiguously this
profile's are removed: ``code_memory_links`` joins a code node to an SLM fact,
and facts carry a profile. The graph of the source code stays, because it
describes a repository rather than a person, and the result says plainly that
it was left.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_FTS_SUFFIXES = ("_fts", "_fts_data", "_fts_idx", "_fts_content", "_fts_docsize", "_fts_config")


def erase_code_graph(
    data_root: Path, *, sole_profile: bool, owned_fact_ids: list[str] | None,
) -> dict:
    """Erase the profile's share; ``owned_fact_ids=None`` with ``sole_profile=False`` is a failure.

    Returns ``{"rows_deleted", "scope", "retained_reason"?, "error"?}``.
    """
    result: dict = {"rows_deleted": 0}
    path = Path(data_root) / "code_graph.db"
    if not path.exists():
        return result
    try:
        conn = sqlite3.connect(str(path))
        conn.isolation_level = None  # autocommit so VACUUM can run
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()]
            conn.execute("BEGIN")
            if sole_profile:
                total = _delete_everything(conn, tables)
                result["scope"] = "whole_graph"
            else:
                result["scope"] = "links_only"
                result["retained_reason"] = (
                    "another profile shares this graph; the source-code structure describes "
                    "a repository, not this person, and deleting it would erase another data "
                    "subject's records"
                )
                total = _delete_links(conn, tables, owned_fact_ids)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("COMMIT")
            conn.execute("VACUUM")  # outside any transaction
            result["rows_deleted"] = total
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("GDPR erase: code_graph.db wipe failed: %s", exc)
        result["error"] = str(exc)
    return result


def _delete_everything(conn: sqlite3.Connection, tables: list[str]) -> int:
    total = 0
    for table in tables:
        if table.endswith(_FTS_SUFFIXES):
            continue  # deleting the base rows handles the index
        total += conn.execute(f"DELETE FROM {table}").rowcount  # noqa: S608
    return total


def _delete_links(conn: sqlite3.Connection, tables: list[str],
                  owned_fact_ids: list[str] | None) -> int:
    if owned_fact_ids is None:
        # Not knowing which links are this person's is a failure to erase,
        # not an empty erasure.
        raise sqlite3.OperationalError(
            "could not list this profile's facts, so its code links cannot be identified")
    if "code_memory_links" not in tables or not owned_fact_ids:
        return 0
    return conn.execute(
        "DELETE FROM code_memory_links WHERE slm_fact_id IN (SELECT value FROM json_each(?))",
        (json.dumps(owned_fact_ids),),
    ).rowcount
