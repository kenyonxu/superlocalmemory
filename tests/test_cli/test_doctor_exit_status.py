"""Doctor's process status must agree with the complete diagnostic report."""

import json
import os
import subprocess
import sys
from argparse import Namespace

import pytest

from superlocalmemory.cli.commands import cmd_doctor


@pytest.mark.parametrize("use_json", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_doctor_exits_one_only_for_failures(tmp_path, capsys, use_json, failed):
    if failed:
        (tmp_path / "migration-error-test.log").write_text("synthetic upgrade failure")
        with pytest.raises(SystemExit) as exc:
            cmd_doctor(Namespace(json=use_json, quick=True))
        assert exc.value.code == 1
    else:
        assert cmd_doctor(Namespace(json=use_json, quick=True)) is None
    out = capsys.readouterr().out
    if use_json:
        data = json.loads(out)["data"]
        assert (data["summary"]["failed"] > 0) is failed
        assert any(c["name"] == "Daemon" for c in data["checks"])
        if not failed:
            assert data["summary"]["warned"] > 0
    else:
        assert "Summary:" in out
        assert ("0 failed" in out) is not failed


@pytest.mark.parametrize("failed", [False, True])
def test_actual_cli_exit_code_and_json_agree(tmp_path, failed):
    # Install the guard before importing product code or resolving its paths.
    script = (
        "import tests._isolation_plugin; import os, sys; from pathlib import Path; "
        "root=Path(os.environ['SLM_DATA_DIR']); "
        + ("(root/'migration-error-test.log').write_text('synthetic upgrade failure'); "
           if failed else "")
        + "from superlocalmemory.cli.main import main; "
        "sys.argv=['slm','doctor','--quick','--json']; main()"
    )
    run = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        env=dict(os.environ), timeout=30,
    )
    assert run.returncode == int(failed), run.stderr
    payload = json.loads(run.stdout)
    assert (payload["data"]["summary"]["failed"] > 0) is failed


def test_doctor_help_documents_status_and_quick_daemon_check(monkeypatch, capsys):
    from superlocalmemory.cli.main import main

    monkeypatch.setattr(sys, "argv", ["slm", "doctor", "--help"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "Exit codes" in text and "1" in text and "0" in text
    assert "warnings" in text.lower() and "fail" in text.lower()
    assert "including daemon health" in text


@pytest.mark.parametrize("code", [1, 2])
def test_report_only_caller_tolerates_only_failure_status(monkeypatch, code):
    from superlocalmemory.cli import commands
    from tests.helpers.doctor import run_doctor_report

    def exit_with_status(args):
        raise SystemExit(code)

    monkeypatch.setattr(commands, "cmd_doctor", exit_with_status)
    if code == 1:
        assert run_doctor_report(Namespace()) is None
    else:
        with pytest.raises(SystemExit) as exc:
            run_doctor_report(Namespace())
        assert exc.value.code == code
