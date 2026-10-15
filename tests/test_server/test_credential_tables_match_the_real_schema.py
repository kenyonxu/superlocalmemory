"""The export scrub list names tables where they really live.

PR 251 re-review N4: CREDENTIAL_TABLES listed ``upload_links`` in media.db,
but upload links live in ``media/uploads.db``, a file no recovery point or
export ever contains. The entry scrubbed nothing and the export manifest
claimed a table media.db does not have. These checks run against the real
schema, not a hand-written one.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from superlocalmemory.infra.backup_credentials import CREDENTIAL_TABLES, OMISSION

_SECRETISH = re.compile(r"(password|token_hash|secret|hmac)", re.IGNORECASE)


def _tables(db: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    with sqlite3.connect(db) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in names:
            try:  # vector tables need an extension this check does not load
                found[name] = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            except sqlite3.OperationalError:
                found[name] = []
    return found


def test_every_listed_table_exists_in_the_store_it_names(engine_with_mock_deps):
    from superlocalmemory.access.rbac import RbacEngine

    db = Path(engine_with_mock_deps._config.db_path)
    RbacEngine(str(db))
    real = {"memory.db": _tables(db)}
    for store, tables in CREDENTIAL_TABLES.items():
        assert store in real, f"{store} is not a store the scrub can see"
        for table in tables:
            assert table in real[store], f"{table} is not in {store}"


def test_no_other_memory_table_holds_password_or_session_hashes(engine_with_mock_deps):
    from superlocalmemory.access.rbac import RbacEngine

    db = Path(engine_with_mock_deps._config.db_path)
    RbacEngine(str(db))
    listed = set(CREDENTIAL_TABLES["memory.db"])
    for table, columns in _tables(db).items():
        if table in listed:
            continue
        assert not [c for c in columns if _SECRETISH.search(c)], (table, columns)


def test_upload_links_live_in_a_file_no_backup_contains(tmp_path):
    from superlocalmemory.media.upload_links import DB_NAME, UploadLinks
    from superlocalmemory.server import backup_bundle

    (tmp_path / "media").mkdir()
    with UploadLinks(tmp_path)._tx():  # the real code creates its own schema
        pass
    uploads = tmp_path / "media" / DB_NAME
    assert "upload_links" in _tables(uploads)
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("CREATE TABLE marker(v TEXT)")
    point = backup_bundle.create_point(tmp_path)
    assert not any(DB_NAME in entry["name"] for entry in point["files"])
    assert "upload_links" not in OMISSION["tables"]
    assert "upload links" in OMISSION["reason"].lower()
