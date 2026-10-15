"""``slm list`` shows stubs, marked, in both outputs (review H3)."""

from __future__ import annotations

import json
from argparse import Namespace

from superlocalmemory.cli import commands
from tests.test_cli.test_list_kind_filter import _save, fake_engine  # noqa: F401


def test_list_marks_a_stub_in_json_and_text(fake_engine, capsys) -> None:  # noqa: F811
    stub = _save(fake_engine._db, 'Session ID: 9f8e7d6c5b4a')
    real = _save(fake_engine._db, 'Zephyr uses PostgreSQL.')
    commands.cmd_list(Namespace(json=True, limit=10, kind=''))
    results = json.loads(capsys.readouterr().out)['data']['results']
    assert {r['fact_id']: r['stub'] for r in results} == {stub: True, real: False}
    commands.cmd_list(Namespace(json=False, limit=10, kind=''))
    lines = [line for line in capsys.readouterr().out.splitlines() if 'Session ID' in line]
    assert lines and '(stub)' in lines[0]
