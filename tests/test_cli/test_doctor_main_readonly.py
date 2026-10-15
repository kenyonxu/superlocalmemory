"""Exercise actual dispatch without the test isolation auto-spawn suppression."""

import json
import os
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("quick", [False, True])
@pytest.mark.parametrize("use_json", [False, True])
@pytest.mark.parametrize("dead", [False, True])
def test_main_doctor_never_launches_daemon(tmp_path, free_port, quick, use_json, dead):
    script = textwrap.dedent("""
        import json, os, sys
        from pathlib import Path
        from dataclasses import replace

        attempts = []
        def audit(event, args):
            if (event == 'subprocess.Popen'
                    and 'superlocalmemory.server.unified_daemon' in str(args[1])):
                attempts.append(True)
                raise RuntimeError('diagnostics must not launch the daemon')
            if event == 'socket.connect' and args[1][1] in (8765, 8767):
                raise RuntimeError('live port forbidden')
            if event in ('open', 'os.listdir', 'os.scandir', 'sqlite3.connect') and args:
                if isinstance(args[0], (str, bytes)):
                    path = Path(os.fsdecode(args[0])).resolve()
                    live = Path('/Users/yourusername/.superlocalmemory')
                    if path == live or live in path.parents:
                        raise RuntimeError('live data forbidden')
        sys.addaudithook(audit)
        if os.environ['PROBE_DEAD'] == '1':
            from superlocalmemory.infra.daemon_identity import build_descriptor, write_descriptor
            write_descriptor(replace(build_descriptor(
                port=int(os.environ['SLM_DAEMON_PORT']), version='test'), pid=2000000000))
        from superlocalmemory.cli.main import main
        sys.argv = ['slm', 'doctor'] + json.loads(os.environ['PROBE_FLAGS'])
        try:
            main()
        finally:
            Path(os.environ['SLM_DATA_DIR'], 'spawn-count').write_text(str(len(attempts)))
    """)
    flags = (["--quick"] if quick else []) + (["--json"] if use_json else [])
    env = dict(os.environ, SLM_DATA_DIR=str(tmp_path), SLM_DAEMON_PORT=str(free_port),
               PROBE_FLAGS=json.dumps(flags), PROBE_DEAD=str(int(dead)))
    env.pop("SLM_TEST_ISOLATION", None)
    env.pop("SLM_TEST_ALLOW_DAEMON_SPAWN", None)
    run = subprocess.run([sys.executable, "-c", script], env=env,
                         capture_output=True, text=True, timeout=30)
    if use_json and run.stdout:
        daemon_detail = next(c for c in json.loads(run.stdout)["data"]["checks"]
                             if c["name"] == "Daemon")["detail"]
    else:
        daemon_detail = next((line for line in run.stdout.splitlines() if "Daemon:" in line), "")
    assert run.returncode == int(dead), f"{daemon_detail}\n{run.stderr}"
    assert (tmp_path / "spawn-count").read_text() == "0"
    if use_json:
        row = next(c for c in json.loads(run.stdout)["data"]["checks"] if c["name"] == "Daemon")
        assert row["status"] == ("FAIL" if dead else "WARN")
    else:
        assert ("[FAIL] Daemon:" if dead else "[WARN] Daemon:") in run.stdout
