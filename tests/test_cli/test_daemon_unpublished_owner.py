"""A real race loser must wait while the winner still owns the instance lock."""

import os
import subprocess
import sys
import textwrap
import time

import pytest

from superlocalmemory.cli import daemon


@pytest.mark.parametrize("publish", [True, False])
def test_exited_child_waits_for_unpublished_live_owner(tmp_path, free_port, monkeypatch, publish):
    script = textwrap.dedent("""
        import json, os, sys, time
        from http.server import HTTPServer, BaseHTTPRequestHandler
        from superlocalmemory.infra.instance_lock import get_instance_lock
        from superlocalmemory.infra.daemon_identity import build_descriptor, write_descriptor
        lock = get_instance_lock()
        assert lock.try_acquire()
        print('locked', flush=True)
        sys.stdin.readline()
        time.sleep(0.9)
        if sys.argv[1] == 'no':
            lock.release()
            sys.exit(0)
        record = build_descriptor(port=int(os.environ['SLM_DAEMON_PORT']), version='test')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(record.public_health_fields()).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', record.port), Handler)
        write_descriptor(record)
        server.serve_forever()
    """)
    env = dict(os.environ, SLM_DAEMON_PORT=str(free_port))
    with subprocess.Popen([sys.executable, "-c", script, "yes" if publish else "no"],
                          env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True) as winner:
        try:
            assert winner.stdout.readline().strip() == "locked"
            assert daemon.instance_lock_is_held()
            assert daemon.read_descriptor() is None
            # The actual losing child has already exited and is reaped.
            with subprocess.Popen([sys.executable, "-c", "pass"], env=env) as loser:
                loser.wait(timeout=5)
                winner.stdin.write("go\n")
                winner.stdin.flush()
                began = time.monotonic()
                ready = daemon._wait_for_daemon(timeout=4, proc=loser)
                elapsed = time.monotonic() - began
                assert ready is publish
                assert elapsed >= 0.9, "failed before the winner could publish or release its lock"
                assert elapsed < 3, "failed to notice that the owner published or exited"
                assert loser.returncode == 0
        finally:
            if winner.poll() is None:
                winner.terminate()
            winner.wait(timeout=5)


def test_unpublished_owner_cannot_extend_start_budget(monkeypatch):
    from unittest.mock import MagicMock

    child = MagicMock()
    child.poll.return_value = 0
    monkeypatch.setattr(daemon, "read_descriptor", lambda: None)
    monkeypatch.setattr(daemon, "instance_lock_is_held", lambda: True)
    sleeps = []
    monkeypatch.setattr(daemon.time, "sleep", sleeps.append)
    assert daemon._wait_for_daemon(timeout=4, proc=child) is False
    assert sum(sleeps) == 4
