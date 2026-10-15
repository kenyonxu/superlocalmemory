"""Resolve the picture channel's visible source set from current fact authority."""
from superlocalmemory.storage.database import _scope_where


def visible_memories(db, profile_id, *, include_global=False, include_shared=False):
    scope, args = _scope_where(profile_id, include_global=include_global,
                               include_shared=include_shared, prefix="f")
    rows = db.execute(
        "SELECT DISTINCT f.memory_id FROM atomic_facts f JOIN memories m "
        "ON m.memory_id=f.memory_id WHERE " + scope + db.current_fact_clause("f")
        + " AND json_extract(m.metadata_json,'$._slm_source.type') IN ('media','document')",
        tuple(args),
    )
    return [r["memory_id"] for r in rows]


def visible_facts(db, memory_ids, profile_id, *, include_global=False, include_shared=False):
    scope, args = _scope_where(profile_id, include_global=include_global,
                               include_shared=include_shared, prefix="f")
    found = []
    for start in range(0, len(memory_ids), 500):
        part = memory_ids[start:start + 500]
        rows = db.execute(
            "SELECT f.fact_id,f.memory_id FROM atomic_facts f WHERE " + scope
            + db.current_fact_clause("f") + " AND f.memory_id IN ("
            + ",".join("?" * len(part)) + ")", (*args, *part),
        )
        found.extend((r["fact_id"], r["memory_id"]) for r in rows)
    return found
