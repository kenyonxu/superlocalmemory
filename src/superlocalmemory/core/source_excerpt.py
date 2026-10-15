# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""A verifiable excerpt pointing to the retained full source via its detail route."""

from __future__ import annotations

import re

_EXCERPT = re.compile(
    r"\n…\[content truncated at ingest; full text in source memory"
    r"(?: [\w-]+ at /api/memories/[\w-]+/detail)?\]…\n"
)


def is_source_excerpt(excerpt: str, source: str) -> bool:
    match = _EXCERPT.search(excerpt)
    if match is None:
        return False
    head, tail = excerpt[: match.start()], excerpt[match.end() :]
    return bool(
        head
        and tail
        and len(head) + len(tail) < len(source)
        and source.startswith(head)
        and source.endswith(tail)
    )


def attach_source_pointer(fact, source: str, max_chars: int):
    from superlocalmemory.core.ingest_gate import apply_ingest_gate

    pointer = f" {fact.memory_id} at /api/memories/{fact.memory_id}/detail"
    gate = apply_ingest_gate(source, max_verbatim_chars=max_chars - len(pointer))
    if gate.truncated:
        fact.content = gate.fact_content.replace(
            "full text in source memory", "full text in source memory" + pointer
        )
    return fact
