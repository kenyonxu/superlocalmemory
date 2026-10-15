# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The triggers that keep ``scene_fact_members`` equal to a scene's fact list.

WHY
---
Every scene save (the materializer assigns each new fact to a scene) fires one
of these inside its write transaction. As M039 wrote them, ``json_each(...)
JOIN atomic_facts`` let SQLite put ``atomic_facts`` in the outer loop -- every
fact of the profile, through ``uq_facts_profile_fact`` -- and scan the scene's
member list once per fact. Measured on a copy of a 27,689-fact store (14 Oct
2026): 4.26 s for a 1,607-member scene, on an idle machine, holding memory.db's
write lock; under load it ran long enough to fail mesh heartbeats with
"database is locked" and to keep accepted remembers from committing.

WHAT THIS DOES
--------------
``CROSS JOIN`` fixes the loop order in SQLite: one member at a time, each a
primary-key probe of ``atomic_facts``. Same rows, same positions. Same scene:
16 ms. M039's text stays as it shipped (its DDL is checksummed); ``ensure``
re-creates the two triggers at every engine start when they are not these.
"""

from __future__ import annotations

import sqlite3

NAMES = ("trg_scene_fact_members_insert", "trg_scene_fact_members_update")

_BODY = """
BEGIN
    DELETE FROM scene_fact_members WHERE scene_id = NEW.scene_id;
    INSERT OR IGNORE INTO scene_fact_members
        (profile_id, scene_id, fact_id, position)
    SELECT NEW.profile_id, NEW.scene_id, af.fact_id, CAST(member.key AS INTEGER)
    FROM json_each(
        CASE WHEN json_valid(NEW.fact_ids_json)
             THEN NEW.fact_ids_json ELSE '[]' END
    ) AS member
    CROSS JOIN atomic_facts AS af
      ON af.fact_id = member.value
     AND af.profile_id = NEW.profile_id;
END"""

DDL = (
    f"CREATE TRIGGER IF NOT EXISTS {NAMES[0]}\nAFTER INSERT ON memory_scenes{_BODY};\n\n"
    f"CREATE TRIGGER IF NOT EXISTS {NAMES[1]}\n"
    f"AFTER UPDATE OF profile_id, fact_ids_json ON memory_scenes{_BODY};\n"
)

_MARK = "CROSS JOIN atomic_facts AS af"


def ensure(conn: sqlite3.Connection) -> bool:
    """Re-create the triggers in the indexed form; True when it changed any.

    Nothing to do when the membership table is absent (M039 not reached). The
    caller owns the transaction.
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='scene_fact_members'").fetchone() is None:
        return False
    current = dict(conn.execute(
        f"SELECT name, sql FROM sqlite_master WHERE type='trigger' "
        f"AND name IN ({','.join('?' for _ in NAMES)})", NAMES).fetchall())
    if all(_MARK in (current.get(name) or "") for name in NAMES):
        return False
    for name in NAMES:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    # One statement at a time: executescript() would commit the caller's
    # transaction first.
    for statement in DDL.split(";\n\n"):
        conn.execute(statement.rstrip().rstrip(";"))
    return True


__all__ = ["DDL", "NAMES", "ensure"]
