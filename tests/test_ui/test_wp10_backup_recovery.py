"""Full recovery units, corruption refusal, and offline restore orchestration."""

import importlib
import json
import sqlite3
import zipfile

import pytest


def recovery():
    return importlib.import_module("superlocalmemory.server.backup_bundle")


def test_legacy_verification_is_reachable_on_the_assembled_backup_router():
    from fastapi import FastAPI

    from superlocalmemory.server.routes.backup import router

    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert "/api/backup/recovery/verify-legacy" in paths
    assert not any("/recovery/api/" in path for path in paths)


def seed(root):
    for name in ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db"):
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE marker(value TEXT)")
            conn.execute("INSERT INTO marker VALUES('before')")
    (root / "config.json").write_text(
        json.dumps(
            {
                "mode": "a",
                "llm": {"api_key": "fixture-secret", "provider": "none"},
                "scope": {"recall_include_global": True},
            }
        )
    )


def test_export_contains_every_present_managed_store_and_safe_settings(tmp_path):
    seed(tmp_path)
    b = recovery()
    point = b.create_point(tmp_path)
    export = b.export_point(tmp_path, point["id"])
    with zipfile.ZipFile(export) as archive:
        assert {
            "memory.db",
            "learning.db",
            "audit_chain.db",
            "pending.db",
            "media.db",
            "settings/config.json",
            "recovery.json",
        } <= set(archive.namelist())
        assert b"fixture-secret" not in archive.read("settings/config.json")
        manifest = json.loads(archive.read("recovery.json"))
        assert "credentials" in manifest["omissions"]
    assert b.verify_point(tmp_path, point["id"])["verified"] is True


def test_restoring_does_not_modify_the_verified_backup_inventory(tmp_path):
    seed(tmp_path)
    b = recovery()
    point = b.create_point(tmp_path)
    b.restore_point(tmp_path, point["id"])
    assert b.verify_point(tmp_path, point["id"])["verified"] is True


def test_recovery_stats_include_new_backup_count_bytes_and_time(tmp_path):
    seed(tmp_path)
    point = recovery().create_point(tmp_path)
    from superlocalmemory.server.recovery_inventory import stats

    status = stats(tmp_path, {"backup_count": 0, "total_size_mb": 0, "last_backup": None})
    assert status["backup_count"] == 1
    assert status["total_size_mb"] > 0
    assert status["last_backup"] == point["created"]


def test_corrupt_store_is_refused_before_restore(tmp_path):
    seed(tmp_path)
    b = recovery()
    point = b.create_point(tmp_path)
    (tmp_path / "backups" / ("backup_" + point["id"]) / "memory.db").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="verification"):
        b.verify_point(tmp_path, point["id"])


def test_offline_restore_round_trip_includes_all_stores_and_settings(tmp_path, monkeypatch):
    seed(tmp_path)
    b = recovery()
    point = b.create_point(tmp_path)
    for name in ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db"):
        with sqlite3.connect(tmp_path / name) as conn:
            conn.execute("UPDATE marker SET value='after'")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "mode": "a",
                "llm": {"api_key": "current-key", "provider": "none"},
                "scope": {"recall_include_global": False},
            }
        )
    )
    b.restore_point(tmp_path, point["id"])
    for name in ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db"):
        with sqlite3.connect(tmp_path / name) as conn:
            assert conn.execute("SELECT value FROM marker").fetchone()[0] == "before"
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["scope"]["recall_include_global"] is True
    assert cfg["llm"]["api_key"] == "current-key"


def test_point_paths_cannot_escape_backup_root(tmp_path):
    with pytest.raises(ValueError):
        recovery().verify_point(tmp_path, "../../outside")


def test_changing_reranker_destination_clears_the_live_credential():
    b = recovery()
    current = {
        "cross_encoder_endpoint": "https://current.example/rerank",
        "cross_encoder_api_key": "fixture-current-key",
        "cross_encoder_backend": "openai",
    }
    b._merge_settings(
        current,
        {"cross_encoder_endpoint": "https://old.example/rerank", "cross_encoder_backend": "openai"},
    )
    assert current["cross_encoder_api_key"] == ""


def test_recovery_retention_honors_maximum_and_preserves_pending_target(tmp_path):
    seed(tmp_path)
    b = recovery()
    (tmp_path / "backup_config.json").write_text('{"max_backups":1}')
    first = b.create_point(tmp_path)
    second = b.create_point(tmp_path)
    assert not (tmp_path / "backups" / ("backup_" + first["id"])).exists()
    (tmp_path / "backup-recovery-outcome.json").write_text(
        json.dumps({"state": "queued", "id": second["id"]})
    )
    third = b.create_point(tmp_path)
    assert (tmp_path / "backups" / ("backup_" + second["id"])).exists()
    assert (tmp_path / "backups" / ("backup_" + third["id"])).exists()


def test_worker_lifecycle_argv_is_accepted_by_the_real_cli_parser(tmp_path, monkeypatch):
    import argparse
    import sys
    from types import SimpleNamespace

    from superlocalmemory.cli import _lazy_init
    from superlocalmemory.cli import main as cli

    worker = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    original = argparse.ArgumentParser.parse_args
    seen = []

    class Captured(Exception):
        pass

    def parse(parser, *args, **kwargs):
        raise Captured(original(parser, *args, **kwargs))

    def command(args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(sys, "argv", ["slm", *args[3:]])
            patch.setattr(argparse.ArgumentParser, "parse_args", parse)
            patch.setattr(_lazy_init, "_ensure_initialized", lambda: None)
            with pytest.raises(Captured) as parsed:
                cli.main()
            seen.append(parsed.value.args[0])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(worker.subprocess, "run", command)
    worker.control(tmp_path, "stop")
    worker.control(tmp_path, "start")
    assert [args.action for args in seen] == ["stop", "start"]


def test_unlisted_settings_cannot_bypass_verification(tmp_path):
    seed(tmp_path)
    b = recovery()
    point = b.create_point(tmp_path)
    folder = tmp_path / "backups" / ("backup_" + point["id"])
    (folder / "settings" / "adapters.json").write_text('{"transcript":{"enabled":true}}')
    with pytest.raises(ValueError, match="verification"):
        b.verify_point(tmp_path, point["id"])


@pytest.mark.parametrize("survives", [False, True])
def test_only_surviving_verified_originals_are_copied_back(tmp_path, monkeypatch, survives):
    from types import SimpleNamespace

    from superlocalmemory import media

    seed(tmp_path)
    rel = "aa/" + "a" * 64 + ".jpg"
    original = tmp_path / "media" / rel
    original.parent.mkdir(parents=True)
    original.write_bytes(b"fixture original")
    b = recovery()
    point = b.create_point(tmp_path)
    original.unlink()
    known = {rel} if survives else set()
    monkeypatch.setattr(
        media,
        "open_media_store",
        lambda **kwargs: SimpleNamespace(known_relpaths=lambda: known, close=lambda: None),
    )
    b.restore_point(tmp_path, point["id"])
    assert original.exists() is survives


def test_recovery_worker_stops_before_restore_and_restarts_after(tmp_path, monkeypatch):
    seed(tmp_path)
    point = recovery().create_point(tmp_path)
    worker = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    calls = []
    monkeypatch.setattr(worker, "control", lambda root, action: calls.append(action))
    monkeypatch.setattr(worker, "wait_stopped", lambda pid: calls.append("stopped"))
    monkeypatch.setattr(
        worker, "restore_atomically", lambda root, point_id, **kw: calls.append("restore")
    )
    worker.run(tmp_path, point["id"], 123)
    assert calls == ["stop", "stopped", "restore", "start"]
    assert (
        json.loads((tmp_path / "backup-recovery-outcome.json").read_text())["state"] == "complete"
    )


def test_recovery_worker_never_restores_if_stop_fails(tmp_path, monkeypatch):
    worker = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    calls = []

    def refused(root, action):
        calls.append(action)
        raise RuntimeError("stop refused")

    monkeypatch.setattr(worker, "control", refused)
    monkeypatch.setattr(worker, "restore_atomically", lambda *args, **kw: calls.append("restore"))
    worker.run(tmp_path, "fixture", 123)
    assert "restore" not in calls
    assert json.loads((tmp_path / "backup-recovery-outcome.json").read_text())["state"] == "failed"


def test_failed_erasure_replay_never_restarts_a_store_it_could_not_roll_back(
    tmp_path, monkeypatch
):
    from superlocalmemory.server.backup_restore_txn import RollbackFailed

    worker = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    calls = []
    monkeypatch.setattr(worker, "verify_point", lambda *args: {})
    monkeypatch.setattr(worker, "control", lambda root, action: calls.append(action))
    monkeypatch.setattr(worker, "wait_stopped", lambda pid: None)
    monkeypatch.setattr(worker, "create_point", lambda root: {"id": "0123456789abcdef"})

    def replay_failed(*args, **kwargs):
        raise RollbackFailed("erasure replay failed, then the rollback failed")

    monkeypatch.setattr(worker, "restore_atomically", replay_failed)
    worker.run(tmp_path, "fixture", 123)
    assert calls == ["stop"]
