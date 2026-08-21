# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""M046 — a planned future event is prospective memory, not a temporal one.

THE COLLISION
-------------
``FactType.TEMPORAL`` was documented in two places as two different things. The
type's own comment said "time-bounded events with intervals". The router that
assigns it says "a scheduled/planned future event" and matches on
``scheduled|deadline|appointment|planned|tomorrow``. Those are not the same
concept: the second is prospective memory — remembering to do something later.

Meanwhile the retrieval **channel** named "temporal" scores every fact by date
proximity regardless of its type. So one word meant a memory type and a scoring
strategy, and the two met in the fusion step with nothing to distinguish them.

Renaming the type is not cosmetic. The surface that answers "what is coming up"
selects ``WHERE fact_type = 'temporal'`` in raw SQL — a reader looking for
prospective memory had to know it was filed under a word that also names a
scoring strategy applied to everything.

WHY THE TABLE HAS TO BE REBUILT
-------------------------------
``atomic_facts`` carries ``CHECK (fact_type IN ('episodic','semantic','opinion',
'temporal'))``. SQLite cannot alter a CHECK constraint, so a new value requires
the documented rebuild: create the corrected table, copy, drop, rename.

Widening the CHECK to accept both words was considered and rejected. It would
leave an old process able to keep writing the wrong value and succeed, which is
the whole failure this release is trying to close.

ROWID IS PRESERVED, AND THAT IS NOT OPTIONAL
--------------------------------------------
``atomic_facts_fts`` is an external-content FTS5 index: ``content='atomic_facts',
content_rowid='rowid'``. The base table's primary key is TEXT, so it is *not* a
rowid alias — the table has an implicit rowid, and ``SELECT *`` does not include
it. A copy that lets SQLite assign fresh rowids leaves every FTS entry pointing
at a different fact than the one it was built from: searches keep working and
start returning the wrong rows. Nothing raises.

So the copy names ``rowid`` explicitly, and the index is rebuilt afterwards as
well. Either alone would probably do; both cost nothing and the failure mode is
silent.

ALL OR NOTHING
--------------
The runner does not wrap ``apply()`` in a transaction — atomicity is opt-in per
migration, and a migration that drops a table has to opt in. Everything below
happens inside one ``BEGIN IMMEDIATE``, and the row count is compared before the
COMMIT: a copy that lost rows raises, which rolls the whole thing back and leaves
the original table exactly as it was.

``PRAGMA foreign_keys`` is set before the transaction opens, because setting it
inside one is a silent no-op.

FORWARD ONLY
------------
The framework has no rollback. Recovery from a bad outcome is restoring the
pre-migration snapshot. That is why the backup tooling is a hard precondition
for this migration rather than a nice-to-have.
"""

from __future__ import annotations

import logging
import re
import sqlite3

logger = logging.getLogger(__name__)

NAME = "M046_prospective_memory_has_its_own_name"
DB_TARGET = "memory"

_TABLE = "atomic_facts"
_OLD_VALUE = "temporal"
_NEW_VALUE = "prospective"
_FTS = "atomic_facts_fts"

#: Recorded for the runner's DDL hash. ``apply()`` runs instead of this string —
#: the rebuild needs the live table's own definition, which no static script can
#: name. Kept accurate because the hash is what detects a shipped migration
#: being edited afterwards.
DDL = """
-- Rebuild atomic_facts with fact_type CHECK accepting 'prospective',
-- translating every existing 'temporal' row. See apply().
"""

#: Matches the fact_type CHECK list in the table's own DDL, whatever whitespace
#: the installed schema happens to use.
_CHECK_RE = re.compile(
    r"(CHECK\s*\(\s*fact_type\s+IN\s*\()([^)]*)(\)\s*\))",
    re.IGNORECASE | re.DOTALL,
)


def _table_sql(conn: sqlite3.Connection, name: str) -> str | None:
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return row[0] if not isinstance(row, dict) else row.get("sql")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Column names in declared order, tolerant of the connection's row factory."""
    out: list[str] = []
    try:
        for row in conn.execute(f"PRAGMA table_info({table})"):
            if isinstance(row, dict):
                out.append(str(row.get("name", "")))
            else:
                try:
                    out.append(str(row["name"]))
                except (TypeError, IndexError, KeyError):
                    out.append(str(row[1]))
    except sqlite3.Error:
        return []
    return [c for c in out if c]


def _count(conn: sqlite3.Connection, sql: str) -> int:
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error:
        return -1
    if row is None:
        return -1
    if isinstance(row, dict):
        return int(next(iter(row.values())))
    return int(row[0])


def _dependents(conn: sqlite3.Connection) -> list[str]:
    """DDL for every index and trigger attached to the table.

    Captured before the drop and replayed after the rename. Auto-created
    indexes (those backing PRIMARY KEY / UNIQUE) have NULL sql and are recreated
    by the table definition itself, so they are excluded rather than replayed.
    """
    out: list[str] = []
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE tbl_name=? AND type IN ('index','trigger') "
            "AND sql IS NOT NULL",
            (_TABLE,),
        ).fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        sql = row[0] if not isinstance(row, dict) else row.get("sql")
        if sql:
            out.append(str(sql))
    return out


def _check_blocks_new_value(conn: sqlite3.Connection) -> bool:
    """Whether the table's CHECK constraint would reject ``prospective``."""
    sql = _table_sql(conn, _TABLE)
    if not sql:
        return False
    match = _CHECK_RE.search(sql)
    if match is None:
        return False
    return _NEW_VALUE not in match.group(2)


def _has_fact_type(conn: sqlite3.Connection) -> bool:
    """Whether the table carries the column this migration converts.

    A store can have ``atomic_facts`` without ``fact_type``: the runner applies
    migrations against partially-built schemas, and the column arrives with a
    migration that may not have run yet. Without this check the no-constraint
    path issues ``UPDATE ... SET fact_type`` and fails with "no such column",
    and ``verify`` then fails forever on the same store — which is exactly how
    the previous release's M043 became permanently stuck.
    """
    return "fact_type" in _columns(conn, _TABLE)


def verify(conn: sqlite3.Connection) -> bool:
    """End state: no row carries the old value, and the CHECK accepts the new one.

    Deliberately does NOT require that any ``prospective`` rows exist. A store
    with no scheduled events is a normal store, and asserting a non-zero count
    would make this migration fail forever on a fresh install — the mistake a
    migration in the previous release made and had to be repaired for.

    A missing table, or a table without the column, means there is nothing to
    convert and that is the end state. Every branch of ``apply()`` produces what
    this asserts.
    """
    if _table_sql(conn, _TABLE) is None:
        return True
    if not _has_fact_type(conn):
        return True
    if _count(conn, f"SELECT COUNT(*) FROM {_TABLE} "
                    f"WHERE fact_type='{_OLD_VALUE}'") != 0:
        return False
    return not _check_blocks_new_value(conn)


def apply(conn: sqlite3.Connection) -> None:
    """Translate the rows, and rebuild the table if its CHECK stands in the way."""
    if _table_sql(conn, _TABLE) is None:
        # The schema has not been created yet; there is nothing to convert.
        return
    if not _has_fact_type(conn):
        # The column this migration converts does not exist on this store yet.
        return

    if not _check_blocks_new_value(conn):
        # Either already migrated, or a schema variant with no CHECK to fight.
        # A plain UPDATE is enough and touches no index: FTS is external-content
        # over `content`, which this does not change.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                f"UPDATE {_TABLE} SET fact_type=? WHERE fact_type=?",
                (_NEW_VALUE, _OLD_VALUE),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        return

    _rebuild(conn)


def _rebuild(conn: sqlite3.Connection) -> None:
    """The documented table rebuild, all inside one transaction."""
    original = _table_sql(conn, _TABLE)
    if not original:  # pragma: no cover — guarded by the caller
        raise sqlite3.OperationalError(f"M046: cannot read {_TABLE} definition")

    # Rewrite only the CHECK list, leaving every column, default and constraint
    # exactly as the installed schema declared it. Reconstructing the table from
    # PRAGMA table_info would silently drop CHECK constraints and collations.
    def _swap(match: re.Match[str]) -> str:
        values = match.group(2)
        return match.group(1) + values.replace(
            f"'{_OLD_VALUE}'", f"'{_NEW_VALUE}'",
        ) + match.group(3)

    rebuilt = _CHECK_RE.sub(_swap, original, count=1)

    # A rewrite that did not take must stop the migration. Proceeding would
    # create a replacement table carrying the ORIGINAL constraint and then drop
    # the real one — the corrupting outcome this whole module exists to avoid.
    if _NEW_VALUE not in rebuilt:
        raise sqlite3.OperationalError(
            "M046: could not rewrite the fact_type constraint; "
            "refusing to rebuild the table"
        )

    staged = rebuilt.replace(_TABLE, f"{_TABLE}_m046_new", 1)
    if f"{_TABLE}_m046_new" not in staged:  # pragma: no cover — defensive
        raise sqlite3.OperationalError("M046: could not name the staging table")

    columns = _columns(conn, _TABLE)
    if not columns:  # pragma: no cover — defensive
        raise sqlite3.OperationalError(f"M046: {_TABLE} reports no columns")

    dependents = _dependents(conn)
    before = _count(conn, f"SELECT COUNT(*) FROM {_TABLE}")
    if before < 0:  # pragma: no cover — defensive
        raise sqlite3.OperationalError(f"M046: cannot count {_TABLE}")

    # Outside the transaction: inside one, this is a silent no-op.
    #
    # The previous value is read first and put back at the end. Unconditionally
    # switching it ON afterwards would leave the caller's connection in a state
    # it did not ask for — this migration borrows the connection, it does not
    # own its settings.
    try:
        _fk_was = conn.execute("PRAGMA foreign_keys").fetchone()
        fk_was = bool(_fk_was[0]) if _fk_was else False
    except sqlite3.Error:  # pragma: no cover
        fk_was = False
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
    except sqlite3.Error:  # pragma: no cover
        pass

    select_list = ", ".join(
        f"CASE WHEN fact_type='{_OLD_VALUE}' THEN '{_NEW_VALUE}' ELSE fact_type END"
        if col == "fact_type" else f'"{col}"'
        for col in columns
    )
    insert_list = ", ".join(f'"{col}"' for col in columns)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{_TABLE}_m046_new"')
        conn.execute(staged)

        # rowid is named on both sides. See the module docstring: the FTS index
        # is keyed on it and the primary key is TEXT, so letting SQLite assign
        # fresh ones silently repoints every index entry.
        conn.execute(
            f'INSERT INTO "{_TABLE}_m046_new" (rowid, {insert_list}) '
            f"SELECT rowid, {select_list} FROM {_TABLE}"
        )

        after = _count(conn, f'SELECT COUNT(*) FROM "{_TABLE}_m046_new"')
        if after != before:
            raise sqlite3.OperationalError(
                f"M046: copied {after} of {before} rows; rolling back"
            )
        stragglers = _count(
            conn,
            f'SELECT COUNT(*) FROM "{_TABLE}_m046_new" '
            f"WHERE fact_type='{_OLD_VALUE}'",
        )
        if stragglers != 0:
            raise sqlite3.OperationalError(
                f"M046: {stragglers} rows still carry the old value; rolling back"
            )

        # Triggers first — they name the table and block the drop.
        for kind, name in _trigger_and_index_names(conn):
            conn.execute(f'DROP {kind} IF EXISTS "{name}"')

        conn.execute(f"DROP TABLE {_TABLE}")
        conn.execute(f'ALTER TABLE "{_TABLE}_m046_new" RENAME TO {_TABLE}')

        for ddl in dependents:
            try:
                conn.execute(ddl)
            except sqlite3.Error as exc:
                # An index or trigger that will not rebuild is a real problem,
                # but it is recoverable by re-running the schema bootstrap; the
                # converted rows are not recoverable if this rolls back.
                logger.warning("M046: could not restore %r: %s", ddl[:60], exc)

        _rebuild_fts(conn)
        conn.execute("COMMIT")
    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover — best effort
            pass
        raise
    finally:
        try:
            conn.execute(
                f"PRAGMA foreign_keys={'ON' if fk_was else 'OFF'}"
            )
        except sqlite3.Error:  # pragma: no cover
            pass


def _trigger_and_index_names(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE tbl_name=? AND type IN ('index','trigger') "
            "AND sql IS NOT NULL",
            (_TABLE,),
        ).fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        if isinstance(row, dict):
            out.append((str(row.get("type", "")).upper(), str(row.get("name", ""))))
        else:
            out.append((str(row[0]).upper(), str(row[1])))
    return [(k, n) for k, n in out if k and n]


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    """Re-derive the search index from the rebuilt table.

    Belt and braces alongside preserving rowid. Never fatal: a store whose
    search index needs another rebuild is recoverable, and losing the converted
    rows to a rollback over it would not be.
    """
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (_FTS,),
        ).fetchone()
        if exists is None:
            return
        conn.execute(f"INSERT INTO {_FTS}({_FTS}) VALUES('rebuild')")
    except sqlite3.Error as exc:
        logger.warning("M046: search index rebuild deferred: %s", exc)


def repair(conn: sqlite3.Connection) -> None:
    """Re-run apply. Safe: both paths are idempotent and verify their own work."""
    apply(conn)
