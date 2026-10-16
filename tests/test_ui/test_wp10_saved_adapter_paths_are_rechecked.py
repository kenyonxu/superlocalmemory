"""Paths saved before the import-folder rule are checked again before use.

PR 251 round 2: /api/adapters/configure now confines paths, but adapters.json
written earlier (by the old route, or by hand) could still point an importer
anywhere. Every configured path is re-checked with the same rules when the
adapter starts and when its worker reads it: inside <data root>/imports (or
the long-standing drop folder <data root>/import) or an approved folder, no
symlink out, no hidden folder. A bad saved path refuses with a message that
says what to do, and nothing crashes.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from superlocalmemory.ingestion import adapter_manager as manager


@pytest.fixture(autouse=True)
def _no_leftover_workers(monkeypatch):
    """Each test starts with no remembered worker (a fake one would leak into the next)."""
    monkeypatch.setattr(manager, "_CHILDREN", {})


@pytest.fixture
def root(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "imports").mkdir(parents=True)
    monkeypatch.setenv("SLM_DATA_DIR", str(data))
    monkeypatch.setattr(manager, "_SLM_HOME", data)
    monkeypatch.setattr(manager, "_ADAPTERS_CONFIG", data / "adapters.json")
    return data


def outside(tmp_path):
    folder = tmp_path / "private"
    folder.mkdir(exist_ok=True)
    (folder / "mail.mbox").write_text("From x\n")
    (folder / "cal.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    (folder / "notes.txt").write_text("not for import")
    return folder


def save(name, field, value):
    config = {name: {"enabled": True, "tier": "auto", field: str(value)}}
    (manager._adapters_config_path()).write_text(json.dumps(config))


BAD = [
    ("transcript", "watch_dir", lambda root, tmp: outside(tmp)),
    ("gmail", "mbox_path", lambda root, tmp: outside(tmp) / "mail.mbox"),
    ("calendar", "ics_path", lambda root, tmp: outside(tmp) / "cal.ics"),
]


@pytest.mark.parametrize("name,field,where", BAD)
def test_start_refuses_a_saved_path_outside_the_import_folders(
    root, tmp_path, monkeypatch, name, field, where
):
    save(name, field, where(root, tmp_path))
    started = []
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: started.append(a))
    result = manager.start_adapter(name)
    assert result["ok"] is False
    assert "allow-folder" in result["error"] and "Configure" in result["error"]
    assert started == []
    row = next(r for r in manager.list_adapters() if r["name"] == name)
    assert row["state"] == "failed" and row["setup_error"] == result["error"]


def test_a_symlink_out_of_the_import_folder_is_refused_at_start(root, tmp_path, monkeypatch):
    link = root / "imports" / "mail.mbox"
    link.symlink_to(outside(tmp_path) / "mail.mbox")
    save("gmail", "mbox_path", link)
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: pytest.fail("started"))
    assert manager.start_adapter("gmail")["ok"] is False


def test_a_hidden_folder_is_refused_at_start(root, monkeypatch):
    hidden = root / "imports" / ".cache"
    hidden.mkdir()
    save("transcript", "watch_dir", hidden)
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: pytest.fail("started"))
    assert manager.start_adapter("transcript")["ok"] is False


def test_a_path_inside_the_import_folder_still_starts(root, monkeypatch):
    calls = root / "imports" / "calls"
    calls.mkdir()
    save("transcript", "watch_dir", calls)

    class Child:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: Child())
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)
    assert manager.start_adapter("transcript")["ok"] is True


def test_the_old_drop_folder_is_an_import_folder(root):
    from superlocalmemory.ingestion import import_roots

    drop = root / "import"
    drop.mkdir()
    (drop / "mail.mbox").write_text("From x\n")
    assert import_roots.admit(drop / "mail.mbox", root) == (drop / "mail.mbox").resolve()


def test_the_mail_worker_reads_nothing_from_a_bad_saved_path(root, tmp_path):
    from superlocalmemory.ingestion.gmail_adapter import GmailAdapter

    save("gmail", "mbox_path", outside(tmp_path) / "mail.mbox")
    adapter = GmailAdapter()
    adapter._detect_tier()
    assert adapter._mbox_path == ""
    assert adapter._fetch_mbox() == []


def test_the_calendar_worker_reads_nothing_from_a_bad_saved_path(root, tmp_path):
    from superlocalmemory.ingestion.calendar_adapter import CalendarAdapter

    save("calendar", "ics_path", outside(tmp_path) / "cal.ics")
    adapter = CalendarAdapter()
    adapter._detect_tier()
    assert adapter._ics_path == ""
    assert adapter._fetch_ics() == []


def test_the_transcript_worker_exits_cleanly_with_the_reason(root, tmp_path):
    save("transcript", "watch_dir", outside(tmp_path))
    import os

    env = {**os.environ, "SLM_DATA_DIR": str(root)}
    done = subprocess.run(
        [sys.executable, "-m", "superlocalmemory.ingestion.transcript_adapter"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 1
    assert "allow-folder" in done.stdout
    assert "Traceback" not in done.stderr


@pytest.mark.parametrize("exit_code", [0, 1])
def test_an_exited_worker_never_hides_a_refused_path(root, tmp_path, monkeypatch, exit_code):
    """PR 251 re-review N0: a worker whose path was refused reads nothing and exits.

    Its exit must not show "completed" (or "worker exited"): the reason is the path.
    """
    from types import SimpleNamespace

    save("gmail", "mbox_path", outside(tmp_path) / "mail.mbox")
    monkeypatch.setattr(manager, "_is_running", lambda name: (False, None))
    monkeypatch.setattr(manager, "_CHILDREN",
                        {"gmail": SimpleNamespace(pid=4242, poll=lambda: exit_code)})
    row = next(r for r in manager.list_adapters() if r["name"] == "gmail")
    assert row["state"] == "failed"
    assert "allow-folder" in row["setup_error"]
