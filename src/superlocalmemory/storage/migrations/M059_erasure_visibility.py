"""Keep visibility caches current when erasure starts or its heal removes intent."""

from __future__ import annotations

import sqlite3

from superlocalmemory.storage.fact_search_changes import RETAINED_CHANGES

NAME = 'M059_erasure_visibility'
DB_TARGET = 'memory'
DOWNGRADE_FLOOR = 51
_INDEX = 'idx_projection_tombstones_memory'
_EVENTS = {'insert': ('INSERT', 'NEW'), 'delete': ('DELETE', 'OLD'),
           'update': ('UPDATE', 'OLD')}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _trigger(event: str) -> str:
    timing, row = _EVENTS[event]
    new_row = ("INSERT INTO fact_search_changes(profile_id,fact_id,what) "
               "VALUES (NEW.profile_id,NEW.fact_id,'*');" if event == 'update' else '')
    rows = (f"VALUES ({row}.profile_id,{row}.fact_id,'*'); " if event != 'delete' else
            "SELECT OLD.profile_id,OLD.fact_id,'*' WHERE EXISTS ("
            "SELECT 1 FROM atomic_facts af WHERE af.profile_id=OLD.profile_id "
            "AND af.fact_id=OLD.fact_id); ")
    # Removing a tombstone only restores visibility while its fact still exists.
    # Deleted facts already have their own change event. Logging again after a
    # profile sweep emptied the log would recreate that profile's erased rows.
    return (
        f'CREATE TRIGGER IF NOT EXISTS trg_erasure_visibility_{event} '
        f'AFTER {timing} ON projection_tombstones BEGIN '
        f"INSERT INTO fact_search_changes(profile_id,fact_id,what) "
        f"{rows}{new_row}"
        f'DELETE FROM fact_search_changes WHERE seq <= '
        f'(SELECT MAX(seq) FROM fact_search_changes)-{RETAINED_CHANGES}; END'
    )


_INDEX_DDL = (f'CREATE INDEX IF NOT EXISTS {_INDEX} ON projection_tombstones'
              '(profile_id,memory_id)')
DDL = ';\n'.join((_INDEX_DDL, *(_trigger(e) for e in _EVENTS))) + ';'


def apply(conn: sqlite3.Connection) -> None:
    tables = _tables(conn)
    if 'projection_tombstones' not in tables:
        return
    conn.execute(_INDEX_DDL)
    if {'fact_search_changes', 'atomic_facts'} <= tables:
        for event in _EVENTS:
            conn.execute(_trigger(event))


def verify(conn: sqlite3.Connection) -> bool:
    tables = _tables(conn)
    if 'projection_tombstones' not in tables:
        return True
    needed = {_INDEX}
    if {'fact_search_changes', 'atomic_facts'} <= tables:
        needed |= {f'trg_erasure_visibility_{e}' for e in _EVENTS}
    present = {r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
    return needed <= present


def repair(conn: sqlite3.Connection) -> None:
    apply(conn)
