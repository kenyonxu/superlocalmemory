# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A store that holds every awkward column type the schema can hold.

The first 4.1.25 audit found both exports dying on ``bytes is not JSON
serializable`` because ``atomic_facts.embedding`` is declared TEXT and holds a
BLOB on a store the engine wrote. No export test had ever stored a row the way
the engine does, so none of them could see it. This builds that store: BLOB
embeddings in TEXT-declared columns, BLOBs in other tables, a thumbnail BLOB in
``media.db``, a model-state BLOB in ``learning.db``, text that is not valid
UTF-8, an infinite REAL, non-Latin text, and audit rows in ``audit_chain.db``.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

PROFILE = "default"
EMBEDDING_BYTES = struct.pack("<768f", *[0.25] * 768)
FISHER_BYTES = struct.pack("<768f", *[0.5] * 768)
THUMB_BYTES = b"\x00\x01RIFF-thumb\xff\xfe"
MODEL_STATE_BYTES = b"\x80\x04model-state\x00\xff"
HINDI = "मुझे चाय पसंद है \U0001f375"


def _add_fact(db: Any, fact_id: str, memory_id: str, content: str, *,
              embedding: bytes | None) -> None:
    db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, metadata_json) "
        "VALUES (?, ?, ?, '{}')",
        (memory_id, PROFILE, f"memory {content}"),
    )
    db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, embedding, "
        "fisher_mean, fisher_variance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fact_id, memory_id, PROFILE, content, embedding,
         FISHER_BYTES if embedding else None, FISHER_BYTES if embedding else None,
         "2026-10-01T10:00:00+00:00"),
    )


def build_awkward_store(engine: Any, *, with_sidecars: bool = True) -> dict[str, Any]:
    """Fill ``engine``'s store (and its data root) with awkward rows."""
    db = engine._db
    db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
               (PROFILE, PROFILE))
    _add_fact(db, "f-embedded", "m-embedded", "Alice drinks green tea in Pune.",
              embedding=EMBEDDING_BYTES)
    _add_fact(db, "f-hindi", "m-hindi", HINDI, embedding=EMBEDDING_BYTES)
    _add_fact(db, "f-plain", "m-plain", "A note that has no embedding yet.", embedding=None)
    # Text that is not valid UTF-8, and an infinite REAL: both are storable.
    db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, confidence) "
        "VALUES ('f-badutf8', 'm-plain', ?, CAST(x'41ff42' AS TEXT), 1e999)", (PROFILE,))
    db.execute(
        "INSERT INTO polar_embeddings (fact_id, profile_id, radius, angle_indices, qjl_bits) "
        "VALUES ('f-embedded', ?, 1.5, ?, ?)", (PROFILE, b"\x01\x02\x03", b"\xff\x00"))
    root: Path = db.db_path.parent
    out: dict[str, Any] = {
        "root": root, "fact_ids": ["f-embedded", "f-hindi", "f-plain", "f-badutf8"]}
    if with_sidecars:
        out.update(_sidecars(db, root))
    return out


def _sidecars(db: Any, root: Path) -> dict[str, Any]:
    import sqlite3

    from superlocalmemory.compliance.audit import AuditChain
    from superlocalmemory.media.store import MediaStore

    store = MediaStore(root / "media.db")
    media_id = store.insert_item(
        profile_id=PROFILE, kind="image", source_sha256="ab" * 32, mime="image/jpeg",
        bytes=2048, width=640, height=480, anchor_memory_id="m-embedded",
        exif_json={"Make": "Acme"}, origin="tool", thumb_webp=THUMB_BYTES)
    store.insert_document(document_id="d" * 32, profile_id=PROFILE, sha256="cd" * 32,
                          title="Lease", mime="application/pdf", bytes=5000, source_relpath="")
    store.close()

    learning = sqlite3.connect(str(root / "learning.db"))
    columns = {row[1] for row in learning.execute("PRAGMA table_info(learning_model_state)")}
    if "state_bytes" in columns:
        learning.execute(
            "INSERT INTO learning_model_state (profile_id, state_bytes, bytes_sha256, "
            "feature_names, trained_at, updated_at) "
            "VALUES (?, ?, 'x', '[]', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')",
            (PROFILE, MODEL_STATE_BYTES))
        learning.commit()
    learning.close()

    chain = AuditChain(root / "audit_chain.db")
    chain.log("store", agent_id="claude", profile_id=PROFILE, content_hash="c1")
    chain.log("store", agent_id="someone-else", profile_id="other-profile", content_hash="c2")
    return {"media_id": media_id}
