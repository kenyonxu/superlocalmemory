"""Entity members from the association index, with legacy coverage fallback."""

from superlocalmemory.storage.entity_index import facts_for_entity, is_complete


def entity_facts(db, entity_id: str, profile_id: str, *, limit: int = -1):
    """Use indexed exact ids once the historical association backfill finished.

    Reuse the storage lookup's canonical visibility and stale-row checks. The
    fallback is required during upgrades: M028's original partial index is
    not a complete source until its durable coverage repair finishes.
    """
    return facts_for_entity(db, entity_id, profile_id, limit=limit, indexed=is_complete(db))
