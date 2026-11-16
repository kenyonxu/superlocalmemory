"""The GitHub star ask: shown to people at a terminal, once, and never to machines."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from superlocalmemory.cli import star_prompt
from superlocalmemory.cli.star_prompt import ASK_LINE, REPO_URL

RESULT = {
    "results": [
        {"fact_id": "f1", "content": "alpha", "score": 0.9, "confidence": 0.9}
    ],
    "retrieval_time_ms": 3,
}


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def tty(monkeypatch, capsys):
    """capsys replaces stdout; give that object an isatty we control."""
    def _set(value: bool):
        out = star_prompt.sys.stdout
        monkeypatch.setattr(out, "isatty", lambda: value, raising=False)

    return _set


def _recall_args(**kw):
    base = dict(query="q", limit=5, json=False, kind="", window="", as_of="",
                known_as_of="", valid_at="", include_global=None, include_shared=None)
    return Namespace(**{**base, **kw})


def _run_recall(monkeypatch, args, result=RESULT):
    import superlocalmemory.cli.daemon as daemon
    from superlocalmemory.cli import commands

    monkeypatch.setattr(daemon, "is_daemon_running", lambda: True)
    monkeypatch.setattr(daemon, "daemon_request", lambda *a, **k: result)
    commands.cmd_recall(args)


def _flag(data_root):
    path = data_root / "ui_state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_setup_completion_shows_ask_on_tty(monkeypatch, capsys, tty):
    from superlocalmemory.cli import deployment, setup_wizard_flow

    monkeypatch.setattr(deployment, "runtime_port", lambda: 8765)
    tty(True)
    setup_wizard_flow._completion_2(SimpleNamespace(config=None), None)
    out = capsys.readouterr().out
    assert out.index(REPO_URL) < out.index(ASK_LINE)
    assert out.rstrip().endswith(REPO_URL)
    assert out.count(REPO_URL) == 2


def test_setup_completion_silent_without_tty(monkeypatch, capsys, tty):
    from superlocalmemory.cli import deployment, setup_wizard_flow

    monkeypatch.setattr(deployment, "runtime_port", lambda: 8765)
    tty(False)
    setup_wizard_flow._completion_2(SimpleNamespace(config=None), None)
    assert ASK_LINE not in capsys.readouterr().out


def test_help_footer_only_on_tty(capsys, tty):
    from superlocalmemory.cli import commands

    tty(True)
    commands.cmd_help(Namespace(topic=None))
    assert "Like SLM? Star it: " + REPO_URL in capsys.readouterr().out
    tty(False)
    commands.cmd_help(Namespace(topic=None))
    assert "Star it" not in capsys.readouterr().out


def test_first_recall_asks_once_then_sets_flag(monkeypatch, capsys, tty, data_root):
    tty(True)
    _run_recall(monkeypatch, _recall_args())
    first = capsys.readouterr().out
    assert ASK_LINE in first
    assert first.index("alpha") < first.index(ASK_LINE)
    assert _flag(data_root)["star_prompt_shown"] is True

    _run_recall(monkeypatch, _recall_args())
    assert ASK_LINE not in capsys.readouterr().out


def test_recall_json_never_asks_and_leaves_flag_unset(monkeypatch, capsys, tty, data_root):
    tty(True)
    _run_recall(monkeypatch, _recall_args(json=True))
    out = capsys.readouterr().out
    assert ASK_LINE not in out and "github.com" not in out
    assert json.loads(out)  # still valid JSON
    assert _flag(data_root) == {}


def test_recall_non_tty_never_asks(monkeypatch, capsys, tty, data_root):
    tty(False)
    _run_recall(monkeypatch, _recall_args())
    assert ASK_LINE not in capsys.readouterr().out
    assert _flag(data_root) == {}


def test_recall_quiet_never_asks(monkeypatch, capsys, tty, data_root):
    tty(True)
    _run_recall(monkeypatch, _recall_args(quiet=True))
    assert ASK_LINE not in capsys.readouterr().out
    assert _flag(data_root) == {}


def test_empty_recall_does_not_consume_the_ask(monkeypatch, capsys, tty, data_root):
    tty(True)
    _run_recall(monkeypatch, _recall_args(), result={"results": []})
    assert ASK_LINE not in capsys.readouterr().out
    assert _flag(data_root) == {}


def test_flag_write_failure_does_not_raise_and_does_not_show(monkeypatch, capsys, tty, data_root):
    tty(True)

    def boom(self, *a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(star_prompt.Path, "write_text", boom)
    assert star_prompt.maybe_print_first_recall_ask(_recall_args(), 3) is False
    assert ASK_LINE not in capsys.readouterr().out


def test_corrupt_state_file_fails_closed(monkeypatch, capsys, tty, data_root):
    tty(True)
    (data_root / "ui_state.json").write_text("{not json", encoding="utf-8")
    assert star_prompt.maybe_print_first_recall_ask(_recall_args(), 2) is False
    assert ASK_LINE not in capsys.readouterr().out


def test_existing_state_keys_are_preserved(monkeypatch, capsys, tty, data_root):
    tty(True)
    (data_root / "ui_state.json").write_text('{"theme": "dark"}', encoding="utf-8")
    assert star_prompt.maybe_print_first_recall_ask(_recall_args(), 1) is True
    assert _flag(data_root) == {"theme": "dark", "star_prompt_shown": True}
