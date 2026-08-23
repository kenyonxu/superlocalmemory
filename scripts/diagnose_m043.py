#!/usr/bin/env python3
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Which of M043's checks is failing on this store, and by how much.

WHY THIS EXISTS

When a completed migration's verification stops passing, the daemon reports
``safe repair did not restore M043_quarantine_display_summaries`` and returns 503
on every feature route. That sentence names the migration and not the condition,
and M043 checks five separate things — so there was no way to tell which one,
short of reading the source and running the queries by hand.

Read-only. Opens the database ``mode=ro`` and writes nothing.

USAGE
    python3 diagnose_m043.py [path/to/memory.db]

Defaults to ~/.superlocalmemory/memory.db. It also prints the store the CLI
would use, because a mismatch between that and the one the daemon serves
explains a `slm db migrate` that reports success beside a daemon that reports
failure — the two are looking at different files.
"""

from __future__ import annotations

import os
import sqlite3
import sys


def main() -> int:
    db = os.path.expanduser(
        sys.argv[1] if len(sys.argv) > 1 else "~/.superlocalmemory/memory.db"
    )
    if not os.path.exists(db):
        print(f"no such store: {db}")
        return 2

    try:
        from superlocalmemory.storage.migrations import (
            M043_quarantine_display_summaries as m,
        )
    except ImportError:
        print("run this with the same interpreter that has superlocalmemory "
              "installed, e.g.  python3 -m pip show superlocalmemory")
        return 2

    print(f"HOME                    {os.path.expanduser('~')}")
    print(f"store being checked     {db}")
    try:
        from superlocalmemory.cli.db_migrate import DEFAULT_HOME
        cli_store = DEFAULT_HOME / "memory.db"
        print(f"store the CLI would use {cli_store}")
        if str(cli_store) != str(db):
            print("  *** THESE DIFFER — see the note at the end ***")
    except Exception as exc:  # noqa: BLE001
        print(f"store the CLI would use (could not resolve: {exc})")
    print()

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    def count(sql: str):
        try:
            return m._count(conn, sql)
        except Exception as exc:  # noqa: BLE001
            return f"ERR {exc}"

    has_facts = m._table_exists(conn, "atomic_facts")
    print(f"  atomic_facts table            {has_facts}")
    print(f"  check 1  quarantined column   "
          f"{m._has_column(conn, 'atomic_facts', 'quarantined')}   (must be True)")
    print(f"  check 2  consolidated_summaries "
          f"{m._table_exists(conn, 'consolidated_summaries')}   (must be True)")
    print()

    if m._table_exists(conn, "fact_consolidations"):
        unwithheld = count(
            "SELECT COUNT(*) FROM atomic_facts "
            " WHERE COALESCE(quarantined, 0) = 0 "
            "   AND fact_id IN (" + m._CONSOLIDATOR_ROWS + ")"
        )
        print(f"  check 3  summaries not withheld        {unwithheld}   (must be 0)")
        unpreserved = count("""
            SELECT COUNT(*) FROM atomic_facts af
             WHERE af.fact_id IN (""" + m._CONSOLIDATOR_ROWS + """)
               AND NOT EXISTS (
                     SELECT 1 FROM consolidated_summaries cs
                      WHERE cs.profile_id = af.profile_id
                        AND (cs.summary_id = af.fact_id
                             OR cs.content = af.content)
                   )
        """)
        print(f"  check 4  withheld with no display copy {unpreserved}   (must be 0)")
    else:
        print("  checks 3 and 4  skipped: this store has no provenance ledger")

    if m._table_exists(conn, "fact_retention"):
        wrong = count("SELECT COUNT(*) FROM (" + m._wrongly_hidden(conn) + ")")
        print(f"  check 5  memories wrongly hidden       {wrong}   (must be 0)")
    else:
        print("  check 5         skipped: no fact_retention table")

    print()
    try:
        print(f"  verify() overall -> {m.verify(conn)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  verify() raised: {type(exc).__name__}: {exc}")
    conn.close()

    print()
    print("If the two stores above differ, that is the whole problem: the CLI has")
    print("been repairing one file while the daemon serves another. Point them at")
    print("the same store (check the systemd unit's HOME / WorkingDirectory) and")
    print("run `slm db migrate` again as the user the daemon runs as.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
