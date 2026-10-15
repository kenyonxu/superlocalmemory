# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The one searchable fact a save gets at once (its first-queryable fact).

Used by the save path (``engine_ingestion.write_queryable``) and by the repair
that gives a memory back the fact enrichment wrongly removed
(``storage/own_fact_repair.py``), so both build exactly the same fact.
"""

from __future__ import annotations

import uuid
from typing import Any

from superlocalmemory.encoding.entity_mentions import extract_mentions


def queryable_fact(fact_content: str, *, profile_id: str, scope: str,
                   shared_with: Any, session_id: str, observation_date: str,
                   created_at: str) -> Any:
    """An embedding-free episodic fact holding ``fact_content`` verbatim."""
    from superlocalmemory.storage.models import AtomicFact, FactType

    entities = extract_mentions(fact_content)
    return AtomicFact(
        fact_id=uuid.uuid4().hex[:16],
        profile_id=profile_id,
        scope=scope,
        shared_with=list(shared_with) or None if shared_with else None,
        content=fact_content,
        fact_type=FactType.EPISODIC,
        entities=entities,
        observation_date=observation_date,
        session_id=session_id,
        confidence=0.7,
        importance=0.5,
        created_at=created_at,
    )
