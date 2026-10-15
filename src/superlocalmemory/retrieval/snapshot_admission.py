"""Bounded, fail-closed creation-time admission for point-in-time recall."""
from superlocalmemory.retrieval.temporal_utils import normalize_as_of


def future_created_ids(db, fact_ids, as_of):
    """Creation time is independent of the temporal validity projection."""
    boundary = normalize_as_of(as_of)
    if boundary is None:
        return set()
    from datetime import datetime

    point = datetime.fromisoformat(boundary)
    excluded = set()
    ids = sorted(fact_ids)
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        rows = db.execute("SELECT fact_id,created_at FROM atomic_facts WHERE fact_id IN ("
                          + ",".join("?" * len(part)) + ")", tuple(part))
        seen = set()
        for row in rows:
            seen.add(row["fact_id"])
            created = normalize_as_of(row["created_at"])
            if created is None or datetime.fromisoformat(created) > point:
                excluded.add(row["fact_id"])
        excluded.update(set(part) - seen)
    return excluded


def snapshot_failures(db, fact_ids, profile_id, as_of, *,
                      include_global=False, include_shared=False):
    """A snapshot intersects both clocks even when explicit time axes are supplied."""
    boundary = normalize_as_of(as_of)
    if boundary is None:
        return set()
    temporal = db.get_strict_temporal_inadmissible_fact_ids(
        list(fact_ids), profile_id, known_as_of=boundary, valid_at=boundary,
        include_unknown=False, include_global=include_global, include_shared=include_shared,
    )
    return future_created_ids(db, fact_ids, boundary) | temporal
