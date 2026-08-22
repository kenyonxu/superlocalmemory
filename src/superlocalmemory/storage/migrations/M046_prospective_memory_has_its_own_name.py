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
import sqlite3

logger = logging.getLogger(__name__)

NAME = "M046_prospective_memory_has_its_own_name"
DB_TARGET = "memory"

#: A store this migration has touched must not be opened by a build whose
#: ceiling is below this.
#:
#: Declared per-migration rather than left to the runner's end-of-run stamp,
#: because that stamp is a completion certificate: it is written only when EVERY
#: migration on BOTH databases is recorded complete. So an unrelated failure
#: elsewhere — a deferred migration on the learning database, say — leaves this
#: rebuild applied and the ceiling still at the old value. An older build then
#: passes the guard, opens the store, and its first planned event is rejected by
#: the new constraint and lost. That is the exact outcome the ceiling exists to
#: convert into a refusal to start.
#:
#: Additive and monotonic: the runner raises the stored version to at least this
#: and never lowers it.
BREAKING_VERSION = 46

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


def _referencing_objects(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Triggers and views that NAME this table while living somewhere else.

    ``_dependents`` finds what is attached to the table. This finds what points
    at it. Since SQLite 3.25 an ``ALTER TABLE ... RENAME`` reparses every
    trigger and view in the schema so it can fix up their references, and at
    that moment the old table has already been dropped — so a trigger on
    another table that joins this one makes the rename fail outright:

        error in trigger trg_scene_fact_members_insert:
        no such table: main.atomic_facts

    Two such triggers ship on ``memory_scenes``, which means every store that
    has ever held a scene. Caught by running the whole migration chain against
    a real archive; running this migration on its own does not reach it,
    because the triggers are created by a migration that had not run yet.

    Returned as (kind, name, sql) so each can be dropped before the rename and
    replayed identically afterwards.
    """
    out: list[tuple[str, str, str]] = []
    try:
        rows = conn.execute(
            "SELECT type, name, sql, tbl_name FROM sqlite_master "
            "WHERE type IN ('trigger','view') AND sql IS NOT NULL "
            "AND tbl_name <> ? AND sql LIKE ?",
            (_TABLE, f"%{_TABLE}%"),
        ).fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        kind, name, sql = str(row[0]), str(row[1]), str(row[2])
        # The shadow tables of the external-content FTS index name the table
        # too, and they are handled by the FTS rebuild, not by replay.
        if name.startswith(_FTS):
            continue
        out.append((kind, name, sql))
    return out


def _has_rowid(conn: sqlite3.Connection) -> bool:
    """Whether this table has a rowid to preserve.

    A ``WITHOUT ROWID`` table has none, and naming it in the copy fails with
    "no column named rowid" — which rolls back and leaves the migration retrying
    forever. Asked by trying, for the same reason the constraint is: the answer
    is a property of the table, not of how its definition was written.

    The shipped schema has a rowid and needs it preserved, because the search
    index is keyed on it. A store that does not have one cannot have that index
    either, so there is nothing to keep.
    """
    try:
        conn.execute(f"SELECT rowid FROM {_TABLE} LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def _fts_exists(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (_FTS,),
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _fts_triggers_present(conn: sqlite3.Connection) -> bool:
    """Whether the table still has triggers feeding the search index.

    The rebuild drops every trigger and replays it. Without them the table and
    the index drift apart from the next write onwards, which no row count and no
    constraint check would notice.
    """
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name=? AND sql LIKE ?",
            (_TABLE, f"%{_FTS}%"),
        ).fetchone()
    except sqlite3.Error:
        return False
    if rows is None:
        return False
    count = next(iter(rows.values())) if isinstance(rows, dict) else rows[0]
    return int(count) > 0


def _accepts(conn: sqlite3.Connection, value: str) -> bool | None:
    """Whether the table will accept ``value`` in ``fact_type``. None if unknown.

    Asked by attempting a write inside a savepoint and rolling it back, because
    the constraint is what decides and its DDL text can be spelled several ways.

    An earlier version read the answer out of the CREATE statement with a regex
    that required a bare ``fact_type`` identifier. On a store whose constraint
    was written ``CHECK ("fact_type" IN (...))`` the pattern did not match, the
    migration concluded there was nothing blocking it, took the cheap UPDATE
    path — and then either raised on the first converted row, or, on a store
    with none, committed happily. In that second case ``verify()`` reported the
    migration complete while the table went on rejecting the new value, so every
    planned event stored afterwards was lost to a constraint failure. Nothing
    would ever have repaired it, because the same regex answered the same way
    every time.

    Returns None when there is no row to probe with. The caller treats that as
    "rebuild anyway": on an empty table a rebuild costs microseconds and makes
    the constraint correct by construction, which is a better trade than
    guessing from text.
    """
    try:
        row = conn.execute(f"SELECT rowid FROM {_TABLE} LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    rowid = next(iter(row.values())) if isinstance(row, dict) else row[0]

    try:
        conn.execute("SAVEPOINT m046_probe")
    except sqlite3.Error:
        return None
    try:
        conn.execute(
            f"UPDATE {_TABLE} SET fact_type=? WHERE rowid=?", (value, rowid),
        )
        return True
    except sqlite3.IntegrityError:
        return False
    except sqlite3.Error:
        return None
    finally:
        # Rolled back either way: this asks a question, it does not change data.
        try:
            conn.execute("ROLLBACK TO m046_probe")
            conn.execute("RELEASE m046_probe")
        except sqlite3.Error:  # pragma: no cover — best effort
            pass


def _ddl_mentions_old_value(conn: sqlite3.Connection) -> bool:
    """Is the old value still written into the table definition?

    The fallback for a table with no rows to probe. Deliberately looks for the
    quoted literal rather than the word: the shipped schema carries a comment
    reading "-- Temporal (3-date model)", which sqlite_master preserves, and
    matching that would report every correctly-migrated store as unmigrated.
    """
    sql = _table_sql(conn, _TABLE) or ""
    return f"'{_OLD_VALUE}'" in sql


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

    # Two things must hold: the definition no longer names the old value
    # anywhere, and the table actually accepts the new one. The second is asked
    # of SQLite rather than read out of the DDL, because a constraint's text can
    # be spelled several ways and its behaviour cannot.
    #
    # Deliberately NOT asserted: that the old value is rejected. On a store whose
    # table never had a CHECK, that is unachievable without inventing a
    # constraint it never had, and the schema-version ceiling is what actually
    # stops an older writer.
    if _ddl_mentions_old_value(conn):
        return False

    # The triggers that keep the search index in step with the table. Checked
    # because the rebuild drops and replays them, and a store that ended up
    # without them is not migrated — it is a store that has silently stopped
    # indexing anything written since. Only asserted when the index exists at
    # all: a store without it never had the triggers either.
    if _fts_exists(conn) and not _fts_triggers_present(conn):
        return False

    accepts_new = _accepts(conn, _NEW_VALUE)
    # None means there was no row to probe with, which on an empty table leaves
    # the definition check above as the whole answer.
    return accepts_new is not False


def apply(conn: sqlite3.Connection) -> None:
    """Translate the rows, and rebuild the table if its CHECK stands in the way."""
    if _table_sql(conn, _TABLE) is None:
        # The schema has not been created yet; there is nothing to convert.
        return
    if not _has_fact_type(conn):
        # The column this migration converts does not exist on this store yet.
        return

    # Rebuild only when the definition still names the old value. A store whose
    # table never constrained fact_type at all has nothing to rewrite: its rows
    # still need translating, but inventing a constraint it never had is not this
    # migration's job — the version ceiling is what stops an older writer.
    needs_rebuild = _ddl_mentions_old_value(conn)
    if not needs_rebuild:
        # The constraint is right; only the rows need translating. A plain UPDATE
        # is enough and touches no index: FTS is external-content over
        # `content`, which this does not change.
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

    # Rename the VALUE, not the column reference. Everything else about the
    # table — every column, default, collation and constraint — is left exactly
    # as the installed schema declared it, because reconstructing from
    # PRAGMA table_info silently drops CHECK constraints and collations.
    #
    # Targeting the quoted literal is what makes this work on any spelling of
    # the constraint. An earlier version matched the identifier, which required a
    # bare `fact_type`; a store written `CHECK ("fact_type" IN (...))` did not
    # match and could not be migrated at all. The value is always `'temporal'`
    # regardless of how the column beside it is quoted.
    rebuilt = original.replace(f"'{_OLD_VALUE}'", f"'{_NEW_VALUE}'")

    # A rewrite that did not take must stop the migration. Proceeding would
    # create a replacement table carrying the ORIGINAL constraint and then drop
    # the real one — the corrupting outcome this whole module exists to avoid.
    if f"'{_OLD_VALUE}'" in rebuilt or f"'{_NEW_VALUE}'" not in rebuilt:
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

    # Generated columns are deliberately absent from this list: PRAGMA
    # table_info does not return them (they appear only in table_xinfo, flagged
    # hidden), and inserting into one is an error. So a store that has added one
    # copies correctly without special handling — verified rather than assumed.
    carries_rowid = _has_rowid(conn)

    dependents = _dependents(conn)
    referencing = _referencing_objects(conn)
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

        # rowid is named on both sides where there is one. See the module
        # docstring: the FTS index is keyed on it and the primary key is TEXT,
        # so letting SQLite assign fresh ones silently repoints every index
        # entry. A WITHOUT ROWID table has none to name, and naming it there
        # fails the whole rebuild.
        if carries_rowid:
            conn.execute(
                f'INSERT INTO "{_TABLE}_m046_new" (rowid, {insert_list}) '
                f"SELECT rowid, {select_list} FROM {_TABLE}"
            )
        else:
            conn.execute(
                f'INSERT INTO "{_TABLE}_m046_new" ({insert_list}) '
                f"SELECT {select_list} FROM {_TABLE}"
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

        # And anything elsewhere in the schema that merely POINTS at the table:
        # the rename reparses every trigger and view, and one that joins a table
        # which no longer exists fails the whole statement.
        for kind, name, _sql in referencing:
            conn.execute(f'DROP {kind} IF EXISTS "{name}"')

        conn.execute(f"DROP TABLE {_TABLE}")
        conn.execute(f'ALTER TABLE "{_TABLE}_m046_new" RENAME TO {_TABLE}')

        for _kind, _name, ddl in referencing:
            # Fatal for the same reason the table's own dependents are: these
            # keep a derived table in step, and committing without them leaves
            # it silently stale.
            conn.execute(ddl)

        for ddl in dependents:
            # Fatal, which reverses an earlier judgement here. That judgement
            # was that losing the converted rows to a rollback would be worse
            # than a missing index — and it was simply wrong: a rollback loses
            # NOTHING, because the original table is still sitting there and the
            # migration will be retried.
            #
            # Committing without these is what costs something. Two of the three
            # replayed statements are the triggers that keep the search index in
            # step with the table, so a store that commits without them stops
            # indexing every memory saved from then on. Lexical search quietly
            # goes blind to new writes, and nothing reports it.
            conn.execute(ddl)

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
