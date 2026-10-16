# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The dashboard's backup export holds what the Backup scope page says it holds (UX-35).

It gzipped ``memory.db`` alone while the page listed learning data, audit and code
data and pending operations as included, so a full recovery could not be done from
the download.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile

import pytest

from superlocalmemory.infra.backup_archive import (
    ArchiveVerificationError,
    build_export,
    verify_export,
)

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "code_graph.db")


@pytest.fixture
def root(tmp_path):
    for name in STORES:
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (f"row of {name}",))
        conn.commit()
        conn.close()
    (tmp_path / "config.json").write_text(json.dumps({"api_key": "sk-secret-do-not-bundle"}))
    (tmp_path / ".install_token").write_text("TOK-4f1c9e")
    return tmp_path


def test_every_advertised_store_is_in_the_archive_with_its_checksum(root, tmp_path) -> None:
    out = tmp_path / "export.tar.gz"

    manifest = build_export(root, out)

    names = {s["name"] for s in manifest["stores"]}
    assert set(STORES) <= names
    with tarfile.open(out, "r:gz") as tar:
        inside = {m.name for m in tar}
        assert all(f"slm-backup/set/{n}" in inside for n in STORES)
        assert "slm-backup/EXPORT-MANIFEST.json" in inside
        assert "slm-backup/set/manifest.json" in inside
        stored = json.loads(tar.extractfile("slm-backup/EXPORT-MANIFEST.json").read())
    assert stored["format"] == "slm-backup-export" and stored["stores"] == manifest["stores"]


def test_settings_and_secrets_are_left_out_and_the_manifest_says_so(root, tmp_path) -> None:
    out = tmp_path / "export.tar.gz"
    manifest = build_export(root, out)

    with tarfile.open(out, "r:gz") as tar:
        everything = b"".join(tar.extractfile(m).read() for m in tar if m.isfile())
    assert b"sk-secret-do-not-bundle" not in everything and b"TOK-4f1c9e" not in everything
    omitted = {o["what"] for o in manifest["omitted"]}
    assert {"config.json", "identity and access secrets"} <= omitted
    assert all(o["reason"] for o in manifest["omitted"])


def test_the_archive_restores_each_store_intact(root, tmp_path) -> None:
    out = tmp_path / "export.tar.gz"
    build_export(root, out)
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    with tarfile.open(out, "r:gz") as tar:
        tar.extractall(fresh, filter="data")

    for name in STORES:
        conn = sqlite3.connect(fresh / "slm-backup" / "set" / name)
        try:
            assert conn.execute("SELECT x FROM t").fetchone()[0] == f"row of {name}"
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()


def test_picture_originals_travel_with_the_archive(root, tmp_path) -> None:
    from superlocalmemory.media import files

    sha = "ab" * 32
    rel = files.original_relpath(sha, "png")
    target = files.media_root(root) / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png bytes")
    out = tmp_path / "export.tar.gz"

    manifest = build_export(root, out)

    assert manifest["media_originals"] == 1
    with tarfile.open(out, "r:gz") as tar:
        assert f"slm-backup/media/{rel}" in {m.name for m in tar}


def test_a_corrupted_archive_is_refused_before_it_is_offered(root, tmp_path) -> None:
    out = tmp_path / "export.tar.gz"
    manifest = build_export(root, out)
    manifest["stores"][0]["sha256"] = "0" * 64

    with pytest.raises(ArchiveVerificationError):
        verify_export(out, manifest)


def test_the_archive_is_owner_only(root, tmp_path) -> None:
    import os
    import stat

    out = tmp_path / "export.tar.gz"
    build_export(root, out)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
