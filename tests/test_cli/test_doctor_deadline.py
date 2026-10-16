"""Wall time, bounded payloads and cleanup for real diagnostic IO."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import psutil
import pytest

from superlocalmemory.cli.doctor_daemon import CHECK_TIMEOUT, check_daemon
from superlocalmemory.infra.daemon_identity import build_descriptor, write_descriptor


def test_feature_probe_preserves_explicit_user_session(free_port, monkeypatch):
    from unittest.mock import MagicMock

    from superlocalmemory.cli.doctor_daemon import MAX_HEALTH_BYTES, _fetch_json
    from superlocalmemory.core import outbound_http

    record = build_descriptor(port=free_port, version="test")
    monkeypatch.setenv("SLM_USER_SESSION", "synthetic-diagnostic-session")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"media": {}}'
    open_url = MagicMock(return_value=response)
    monkeypatch.setattr(outbound_http, "urlopen", open_url)
    assert _fetch_json(record, "/api/v3/features", 0.2) == {"media": {}}
    headers = dict(open_url.call_args.args[0].header_items())
    assert headers["X-slm-user-session"] == "synthetic-diagnostic-session"
    assert headers["X-slm-daemon-capability"] == record.capability
    assert headers["X-slm-target-instance"] == record.instance_id
    response.read.assert_called_once_with(MAX_HEALTH_BYTES + 1)
    response.__exit__.assert_called_once()


@pytest.mark.parametrize("behavior", ["silent", "stream", "large", "features", "healthy"])
def test_whole_diagnostic_has_one_deadline(free_port, behavior):
    record = build_descriptor(port=free_port, version="test", pid=os.getpid(),
                              started_at=time.time() - 3600)
    write_descriptor(record)
    stop = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            try:
                if behavior == "silent":
                    stop.wait(2)
                    return
                self.send_response(200)
                self.end_headers()
                if behavior == "stream" or (behavior == "features" and self.path != "/health"):
                    until = time.monotonic() + CHECK_TIMEOUT + 1
                    while time.monotonic() < until and not stop.wait(0.05):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                    return
                payload = (b" " * 100_000 if behavior == "large" else
                           json.dumps(record.public_health_fields() if self.path == "/health"
                                      else {"media": {"restart_required": True}}).encode())
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", free_port), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    children = {p.pid for p in psutil.Process().children()}
    began = time.monotonic()
    try:
        result = check_daemon()
        elapsed = time.monotonic() - began
        assert elapsed < CHECK_TIMEOUT + 0.2, f"diagnostic took {elapsed:.3f}s"
        assert requests.count("/health") == 1
        assert {p.pid for p in psutil.Process().children()} == children
        if behavior == "healthy":
            assert result.status == "PASS"
        elif behavior == "features":
            assert result.status == "PASS" and result.feature_warning
        else:
            # A health reply that cannot be read is an unhealthy daemon, never a warning.
            assert result.status == "FAIL"
            words = ("deadline", "within", "oversized")
            assert any(word in result.detail.lower() for word in words)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipe")
def test_descriptor_read_is_inside_deadline(tmp_path):
    os.mkfifo(tmp_path / "daemon.json")
    # Permit an unbounded reader to finish later so RED does not hang pytest.
    def unblock():
        time.sleep(CHECK_TIMEOUT * 2)
        try:
            fd = os.open(tmp_path / "daemon.json", os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return  # A bounded worker has already closed its reader.
        os.write(fd, b"{}")
        os.close(fd)

    thread = threading.Thread(target=unblock)
    thread.start()
    began = time.monotonic()
    try:
        result = check_daemon()
        assert time.monotonic() - began < CHECK_TIMEOUT + 0.2
        assert result.status in {"WARN", "FAIL"}
    finally:
        thread.join(2)
