"""The bound port must be known before identity and remote runtime construction."""

import os
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("ephemeral", [True, False])
def test_start_server_publishes_bound_port_before_building_remote_runtime(
    tmp_path, monkeypatch, free_port, ephemeral,
):
    from superlocalmemory.infra.daemon_identity import read_descriptor
    from superlocalmemory.server import unified_daemon as daemon

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    for name in ("SLM_DAEMON_PORT", "SLM_DAEMON_INSTANCE_ID", "SLM_DAEMON_CAPABILITY"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setattr(daemon, "_ACTIVE_DAEMON_DESCRIPTOR", None)
    monkeypatch.setattr(daemon, "install_thread_dump_signal", lambda: None)
    monkeypatch.setattr(daemon, "_start_record_guardian", lambda: None)
    observed = []

    def serve(port, host, listener, lock):
        bound_port = listener.getsockname()[1]
        assert 0 < bound_port <= 65535
        assert port == bound_port
        assert int(os.environ["SLM_DAEMON_PORT"]) == bound_port
        assert read_descriptor().port == bound_port
        app = daemon.create_app()
        assert app.state.daemon_descriptor.port == bound_port
        assert app.state.daemon_descriptor.state == "starting"
        runtime = app.state.remote_connection_runtime
        assert runtime.provider.redirect_uri == (
            f"http://127.0.0.1:{bound_port}/api/v3/connections/callback"
        )
        assert runtime.origin._base_url == f"https://127.0.0.1:{bound_port}"
        observed.append(bound_port)

    monkeypatch.setattr(daemon, "_serve_owned", serve)
    daemon.start_server(port=0 if ephemeral else free_port)
    assert len(observed) == 1
    if not ephemeral:
        assert observed == [free_port]


@pytest.mark.parametrize("state", ["starting", "ready"])
@pytest.mark.parametrize("port", [0, -1, 65536, True, "18765"])
def test_origin_refuses_invalid_identity_even_while_starting(state, port):
    from superlocalmemory.remote_connections.origin import CanonicalMcpOrigin

    app = SimpleNamespace(state=SimpleNamespace(
        daemon_descriptor=SimpleNamespace(port=port, state=state),
    ))
    with pytest.raises(ValueError, match="^invalid_origin_port$"):
        CanonicalMcpOrigin(app)
