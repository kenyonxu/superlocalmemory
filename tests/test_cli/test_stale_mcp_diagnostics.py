"""Doctor and host upgrades name the applications still running old code."""

from argparse import Namespace

from superlocalmemory.infra import mcp_processes


def test_doctor_names_only_live_stale_hosts(monkeypatch, tmp_path):
    from superlocalmemory.cli.doctor_mcp import connected_apps_finding

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mcp_processes, "process_identity", lambda pid: "live")
    monkeypatch.setattr(mcp_processes, "installed_version", lambda: "new")
    mcp_processes.register(tmp_path, "one", pid=101, host="hermes", version="old", tools=[])
    status, detail, action = connected_apps_finding()
    assert status == "WARN"
    assert "Hermes" in detail and "Connected apps" in action


def test_upgrade_hosts_reports_stale_apps_even_without_config_files(monkeypatch, tmp_path, capsys):
    from superlocalmemory.cli import host_upgrades

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mcp_processes, "process_identity", lambda pid: "live")
    monkeypatch.setattr(mcp_processes, "installed_version", lambda: "new")
    monkeypatch.setattr(host_upgrades, "_detected_hosts", lambda: [])
    mcp_processes.register(tmp_path, "one", pid=101, host="codex", version="old", tools=[])
    host_upgrades.cmd_upgrade_hosts(Namespace(hosts=[], apply=False, all_detected=False))
    assert "Apps to restart or reconnect: Codex" in capsys.readouterr().out
