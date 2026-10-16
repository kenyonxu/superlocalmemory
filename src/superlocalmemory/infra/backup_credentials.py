# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Accounts, sessions and the sign-in policy are installation state, not memory.

Two rules follow, and this module is where both live:

* A restore never brings these tables back from a recovery point. The rows the
  installation has NOW carry forward into the restored store. Otherwise a
  restore would silently undo a removed user, a revoked session, or the policy
  that requires sign-in (which turns company mode off).
* An export never contains them. A downloaded archive leaves the machine; a
  password hash or a session-token hash in it is an offline cracking target,
  and since no restore reads them back they have no recovery value. This holds
  in personal mode too, so an archive's contents never depend on the mode.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

#: Backed-up store file -> tables that hold accounts, credentials or the sign-in
#: policy. Upload links (``upload_links``) live in ``media/uploads.db``, a file
#: no recovery point or export contains (``infra.backup_media`` copies only
#: content-addressed originals), so they need no entry here.
CREDENTIAL_TABLES: dict[str, tuple[str, ...]] = {
    "memory.db": ("rbac_users", "rbac_memberships", "rbac_sessions", "rbac_settings"),
}

#: Shown in export manifests next to the other deliberate omissions.
OMISSION = {
    "what": "accounts, sessions and the sign-in setting",
    "reason": "password and token hashes never leave this computer; a restore keeps "
              "the accounts this installation has now. Upload links are never in a "
              "backup at all",
    "tables": sorted(t for tables in CREDENTIAL_TABLES.values() for t in tables),
}

Captured = dict[str, dict[str, tuple[list[str], list[tuple]] | None]]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def scrub_store(path: Path, store_name: str) -> bool:
    """Empty the credential tables of one store copy, leaving no bytes behind.

    ``secure_delete`` zeroes freed pages and ``VACUUM`` rebuilds the file, so
    a deleted hash cannot be read back from free space. Returns whether the
    copy holds any of the tables.
    """
    wanted = CREDENTIAL_TABLES.get(store_name, ())
    if not wanted or not Path(path).is_file():
        return False
    with closing(sqlite3.connect(path)) as conn:
        present = [t for t in wanted if t in _tables(conn)]
        if not present:
            return False
        conn.execute("PRAGMA secure_delete = ON")
        with conn:
            for table in present:
                conn.execute(f'DELETE FROM "{table}"')
        conn.execute("VACUUM")
        conn.execute("PRAGMA journal_mode = DELETE")
    return True


def capture(root: Path) -> Captured:
    """Read the live credential tables; ``None`` marks a table that does not exist."""
    captured: Captured = {}
    for store, tables in CREDENTIAL_TABLES.items():
        path = Path(root) / store
        captured[store] = {}
        if not path.is_file():
            continue
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            existing = _tables(conn)
            for table in tables:
                if table not in existing:
                    captured[store][table] = None
                    continue
                cols = _columns(conn, table)
                quoted = ", ".join(f'"{c}"' for c in cols)
                rows = conn.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
                captured[store][table] = (cols, rows)
    return captured


def carry_forward(root: Path, captured: Captured) -> None:
    """Replace the restored credential tables with the captured live rows.

    One transaction per store. A table the installation did not have is
    emptied in the restored copy; a column only one side has is skipped.
    """
    for store, tables in captured.items():
        path = Path(root) / store
        if not path.is_file():
            continue
        with closing(sqlite3.connect(path)) as conn, conn:
            existing = _tables(conn)
            for table, value in tables.items():
                if table in existing:
                    _replace(conn, table, value)


def _replace(conn: sqlite3.Connection, table: str, value) -> None:
    conn.execute(f'DELETE FROM "{table}"')
    if not value:
        return
    cols, rows = value
    keep = [c for c in cols if c in set(_columns(conn, table))]
    if not keep or not rows:
        return
    index = [cols.index(c) for c in keep]
    quoted = ", ".join(f'"{c}"' for c in keep)
    marks = ", ".join("?" for _ in keep)
    conn.executemany(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({marks})',
        [tuple(row[i] for i in index) for row in rows],
    )
