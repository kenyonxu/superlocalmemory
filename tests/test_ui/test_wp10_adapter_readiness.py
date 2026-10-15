import json
import sys
from types import SimpleNamespace

import pytest

from superlocalmemory.ingestion import adapter_manager as manager


@pytest.fixture(autouse=True)
def _no_leftover_workers(monkeypatch):
    """Each test starts with no remembered worker (a fake one would leak into the next)."""
    monkeypatch.setattr(manager, "_CHILDREN", {})


def test_configure_route_has_a_real_request_in_the_assembled_api():
    from fastapi import FastAPI

    from superlocalmemory.server.routes.adapters import router

    app = FastAPI()
    app.include_router(router)
    operation = app.openapi()["paths"]["/api/adapters/configure"]["post"]
    assert not any(p["name"] == "request" for p in operation.get("parameters", []))

@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "_SLM_HOME", tmp_path)
    monkeypatch.setattr(manager, "_ADAPTERS_CONFIG", tmp_path / "adapters.json")


def test_legacy_polling_tiers_are_migrated_on_read(tmp_path):
    (tmp_path / "adapters.json").write_text(
        json.dumps({"gmail": {"enabled": True, "tier": "polling"}, "calendar": {"tier": "polling"}})
    )
    rows = manager.list_adapters()
    assert next(r for r in rows if r["name"] == "gmail")["tier"] == "auto"
    assert json.loads((tmp_path / "adapters.json").read_text())["calendar"]["tier"] == "auto"


def test_invalid_tier_is_rejected_before_persisting(tmp_path):
    with pytest.raises(ValueError, match="tier"):
        manager._save_config({"calendar": {"tier": "mbox"}})
    assert not (tmp_path / "adapters.json").exists()


def test_missing_transcript_folder_is_actionable_not_running():
    manager.enable_adapter("transcript")
    result = manager.start_adapter("transcript")
    assert result["ok"] is False
    assert "folder" in result["error"].lower()
    assert manager.list_adapters()[2]["running"] is False


def test_zombie_pid_is_not_running(tmp_path, monkeypatch):
    (tmp_path / "adapter-transcript.pid").write_text("12345")
    fake = SimpleNamespace(
        pid_exists=lambda pid: True,
        STATUS_ZOMBIE="zombie",
        STATUS_DEAD="dead",
        Process=lambda pid: SimpleNamespace(status=lambda: "zombie"),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    assert manager._is_running("transcript")[0] is False


def test_exited_dashboard_child_cannot_hide_a_new_external_worker(tmp_path, monkeypatch):
    (tmp_path / "imports").mkdir(exist_ok=True)  # transcripts are read from an import folder
    manager._save_config({"transcript": {"enabled": True, "watch_dir": str(tmp_path / "imports")}})
    monkeypatch.setattr(
        manager, "_CHILDREN", {"transcript": SimpleNamespace(pid=123, poll=lambda: 0)}
    )
    monkeypatch.setattr(manager, "_is_running", lambda name: (name == "transcript", 999))
    transcript = next(r for r in manager.list_adapters() if r["name"] == "transcript")
    assert transcript["running"] is True
    assert transcript["state"] == "running"


def test_process_that_exits_immediately_is_failed_with_retry(tmp_path, monkeypatch):
    (tmp_path / "imports").mkdir(exist_ok=True)
    manager._save_config(
        {"transcript": {"enabled": True, "tier": "auto", "watch_dir": str(tmp_path / "imports")}}
    )
    monkeypatch.setattr(
        manager.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=12345, poll=lambda: 1)
    )
    result = manager.start_adapter("transcript")
    assert result["ok"] is False
    assert "retry" in result["error"].lower()


def test_invalid_direct_tier_cannot_silently_fetch_nothing():
    from superlocalmemory.ingestion.calendar_adapter import CalendarAdapter
    from superlocalmemory.ingestion.gmail_adapter import GmailAdapter

    for adapter in [GmailAdapter(tier="polling"), CalendarAdapter(tier="polling")]:
        with pytest.raises(ValueError, match="tier"):
            adapter._detect_tier()


def test_real_setup_phase_emits_supported_tiers(tmp_path):
    from superlocalmemory.cli import setup_wizard, setup_wizard_flow
    from superlocalmemory.ingestion.adapter_config import validate_tier

    state = SimpleNamespace(interactive=False, slm_root=tmp_path)
    for name in sorted(n for n in vars(setup_wizard_flow) if n.startswith("_ingestion_")):
        getattr(setup_wizard_flow, name)(state, setup_wizard)
    saved = json.loads((tmp_path / "adapters.json").read_text())
    assert set(saved) == {"gmail", "calendar", "transcript"}
    for name, entry in saved.items():
        assert validate_tier(name, entry["tier"]) == "auto"
