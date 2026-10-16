"""General autostart and OS restarts obey the same selected namespace policy."""

import pytest


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " true "])
def test_valid_strict_loopback_values_survive_service_install(monkeypatch, value):
    from superlocalmemory.cli.deployment import security_environment

    monkeypatch.setenv("SLM_REQUIRE_API_KEY_LOOPBACK", value)
    assert security_environment()["SLM_REQUIRE_API_KEY_LOOPBACK"] == "1"


def test_general_autostart_uses_saved_port_without_opening_a_socket(monkeypatch, tmp_path):
    from superlocalmemory.cli import daemon

    monkeypatch.delenv("SLM_DAEMON_PORT", raising=False)
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text('{"daemon_port":19283}')
    monkeypatch.setattr(daemon, "_DEFAULT_PORT", 8765)
    monkeypatch.setattr(daemon, "is_daemon_running", lambda: False)
    probes = []
    monkeypatch.setattr(daemon, "_has_tcp_listener", lambda port: probes.append(port) or True)
    assert daemon._start_daemon_subprocess() is False
    assert probes == [19283]
