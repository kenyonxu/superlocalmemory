# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A portability export holds one person's data and is created owner-only (OPS-04, OPS-05).

OPS-04: ``_export_code_graph`` dumped every table of ``code_graph.db`` into every
profile's export, so one workspace's export named another's repositories.
OPS-05: exports written to a path the person chose were created with the process
umask (0644) and left the private data folder, so other accounts could read them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from argparse import Namespace
from pathlib import Path

import pytest

from superlocalmemory.compliance.gdpr import GDPRCompliance
from superlocalmemory.storage import schema as real_schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")

ENGINEERING_SYMBOL = "EngineeringPaymentsRouter"
SALES_SYMBOL = "SalesPrivateRobertContract"


@pytest.fixture
def two_workspaces(tmp_path):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(real_schema)
    for pid in ("engineering", "sales"):
        db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)", (pid, pid))
        db.store_memory(MemoryRecord(memory_id=f"m-{pid}", profile_id=pid, content="x"))
        db.store_fact(AtomicFact(fact_id=f"f-{pid}", memory_id=f"m-{pid}", profile_id=pid,
                                 content=f"{pid} fact"))
    conn = sqlite3.connect(tmp_path / "code_graph.db")
    conn.executescript(
        "CREATE TABLE graph_nodes (node_id TEXT PRIMARY KEY, name TEXT, file_path TEXT);"
        "CREATE TABLE graph_files (file_path TEXT PRIMARY KEY, content_hash TEXT);"
        "CREATE TABLE graph_edges (edge_id TEXT PRIMARY KEY, source_node_id TEXT);"
        "CREATE TABLE code_memory_links (link_id TEXT PRIMARY KEY, code_node_id TEXT,"
        " slm_fact_id TEXT, enriched_description TEXT);"
        f"INSERT INTO graph_nodes VALUES ('n-eng', '{ENGINEERING_SYMBOL}', '/work/eng/pay.py');"
        f"INSERT INTO graph_nodes VALUES ('n-sales', '{SALES_SYMBOL}', '/work/sales/contract.py');"
        "INSERT INTO graph_files VALUES ('/work/eng/pay.py', 'h1');"
        "INSERT INTO graph_files VALUES ('/work/sales/contract.py', 'h2');"
        "INSERT INTO graph_edges VALUES ('e1', 'n-sales');"
        "INSERT INTO code_memory_links VALUES ('L1', 'n-eng', 'f-engineering', 'eng note');"
        "INSERT INTO code_memory_links VALUES ('L2', 'n-sales', 'f-sales', 'sales note');")
    conn.commit()
    conn.close()
    return GDPRCompliance(db, data_root=tmp_path), tmp_path


def test_a_shared_graph_exports_only_the_persons_own_links(two_workspaces) -> None:
    gdpr, _ = two_workspaces

    data = gdpr.export_profile_data("engineering")

    text = json.dumps(data)
    assert ENGINEERING_SYMBOL in text and "/work/eng/pay.py" in text
    assert SALES_SYMBOL not in text and "/work/sales" not in text and "sales note" not in text
    assert [r["link_id"] for r in data["code_graph"]["code_memory_links"]] == ["L1"]
    assert "graph_edges" not in data["code_graph"]
    left_out = {t for note in data["export_manifest"]["not_exported"] for t in note["tables"]}
    assert "graph_edges" in left_out, "the manifest says what was left out of a shared graph"


def test_streamed_export_has_the_same_scope(two_workspaces, tmp_path) -> None:
    gdpr, _ = two_workspaces
    with open(tmp_path / "out.json", "w+b") as fp:
        gdpr.write_profile_export("sales", fp)
        fp.seek(0)
        text = fp.read().decode()
    assert SALES_SYMBOL in text and ENGINEERING_SYMBOL not in text


def test_the_only_workspace_on_the_computer_gets_the_whole_graph(tmp_path) -> None:
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(real_schema)
    db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('solo', 'solo')")
    db.execute("DELETE FROM profiles WHERE profile_id <> 'solo'")
    conn = sqlite3.connect(tmp_path / "code_graph.db")
    conn.execute("CREATE TABLE graph_nodes (node_id TEXT PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO graph_nodes VALUES (?, ?)", [("a", "one"), ("b", "two")])
    conn.commit()
    conn.close()

    graph = GDPRCompliance(db, data_root=tmp_path).export_profile_data("solo")["code_graph"]

    assert len(graph["graph_nodes"]) == 2


# ------------------------------------------------------------------ permissions

def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _export(gdpr_root: Path, target: Path, monkeypatch, capsys):
    from superlocalmemory.cli import gdpr_cmd

    monkeypatch.setenv("SLM_DATA_DIR", str(gdpr_root))
    args = Namespace(profile="engineering", output=str(target), json=False,
                     include_embeddings=False)
    gdpr_cmd._cmd_gdpr_export(args)
    capsys.readouterr()


@pytest.mark.parametrize("umask", [0o022, 0o000])
def test_a_cli_export_is_owner_only_whatever_the_umask(
        two_workspaces, tmp_path, monkeypatch, capsys, umask) -> None:
    _, root = two_workspaces
    target = tmp_path / "out" / "export.json"
    target.parent.mkdir()
    old = os.umask(umask)
    try:
        _export(root, target, monkeypatch, capsys)
    finally:
        os.umask(old)

    assert _mode(target) == 0o600


def test_an_existing_world_readable_target_is_replaced_by_a_private_file(
        two_workspaces, tmp_path, monkeypatch, capsys) -> None:
    _, root = two_workspaces
    target = tmp_path / "export.json"
    target.write_text("old")
    target.chmod(0o644)

    _export(root, target, monkeypatch, capsys)

    assert _mode(target) == 0o600 and json.loads(target.read_text())["profile_id"] == "engineering"


def test_a_stale_part_file_with_broad_permissions_does_not_leak_the_export(
        two_workspaces, tmp_path, monkeypatch, capsys) -> None:
    _, root = two_workspaces
    target = tmp_path / "export.json"
    part = tmp_path / "export.json.part"
    part.write_text("stale")
    part.chmod(0o644)
    seen: list[int] = []
    real = os.replace

    def spy(src, dst):
        seen.append(_mode(Path(src)))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    _export(root, target, monkeypatch, capsys)

    assert seen == [0o600]


def test_a_symlink_at_the_target_is_replaced_not_followed(
        two_workspaces, tmp_path, monkeypatch, capsys) -> None:
    _, root = two_workspaces
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch")
    target = tmp_path / "export.json"
    part = tmp_path / "export.json.part"
    target.symlink_to(victim)
    part.symlink_to(victim)  # a planted link where the working file goes

    _export(root, target, monkeypatch, capsys)

    assert victim.read_text() == "do not touch"
    assert not target.is_symlink() and _mode(target) == 0o600


def test_an_evidence_bundle_is_owner_only(two_workspaces, tmp_path) -> None:
    from superlocalmemory.core.evidence_bundle import export_evidence_bundle

    gdpr, _ = two_workspaces
    destination = tmp_path / "bundle"
    old = os.umask(0o022)
    try:
        export_evidence_bundle(gdpr._db, "engineering", destination)
    finally:
        os.umask(old)

    assert _mode(destination) == 0o700
    files = [p for p in destination.iterdir()]
    assert files and all(_mode(p) == 0o600 for p in files)


def test_an_evidence_bundle_does_not_change_the_permissions_of_a_folder_in_use(
        two_workspaces, tmp_path) -> None:
    from superlocalmemory.core.evidence_bundle import export_evidence_bundle

    gdpr, _ = two_workspaces
    busy = tmp_path / "documents"
    busy.mkdir()
    (busy / "mine.txt").write_text("keep")
    busy.chmod(0o755)

    with pytest.raises(ValueError, match="must be empty"):
        export_evidence_bundle(gdpr._db, "engineering", busy)

    assert _mode(busy) == 0o755
