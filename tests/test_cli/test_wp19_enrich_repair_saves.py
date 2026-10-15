# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``slm enrich`` lists saves that did not finish and fixes them with a confirm."""

import pytest

from superlocalmemory.cli.enrich_cmd import cmd_enrich
from tests.test_cli.test_wp19_enrich_cmd import fake_daemon, interactive, parse

SAVES = {'action': 'repair-saves', 'found': 3, 'label': 'saves that did not finish',
         'sentence': "3 saves didn't finish.", 'undoable': False}


def test_status_lists_it_as_a_suggested_repair_with_its_command(monkeypatch, capsys):
    from tests.test_cli import test_wp19_enrich_cmd as base
    monkeypatch.setitem(base.STATUS, 'suggestions', [SAVES])
    fake_daemon(monkeypatch)
    cmd_enrich(parse('status'))
    out = capsys.readouterr().out
    assert "Can be fixed: 3 saves didn't finish. (slm enrich fix repair-saves)" in out


def test_fix_sends_the_confirm_and_says_it_cannot_be_undone(monkeypatch, capsys):
    calls = fake_daemon(monkeypatch, post={'found': 3, 'fixed': 3, 'run_id': 'fix-9'})
    cmd_enrich(parse('fix', 'repair-saves', '--yes'))
    assert [c[2] for c in calls if c[0] == 'POST'] == [
        {'action': 'repair', 'repair': 'repair-saves', 'confirm': True}]
    out = capsys.readouterr().out
    assert "Fixed 3 of 3. This can't be undone." in out
    assert 'slm enrich undo' not in out


def test_the_question_warns_there_is_no_undo(monkeypatch):
    calls = fake_daemon(monkeypatch)
    asked = []
    monkeypatch.setattr('sys.stdin.isatty', lambda: True, raising=False)
    monkeypatch.setattr('sys.stdout.isatty', lambda: True, raising=False)
    monkeypatch.setattr('builtins.input', lambda prompt: asked.append(prompt) or 'n')
    with pytest.raises(SystemExit):
        cmd_enrich(parse('fix', 'repair-saves'))
    assert "can't be undone" in asked[0]
    assert not [c for c in calls if c[0] == 'POST']


def test_undo_without_an_id_skips_a_run_that_cannot_be_undone(monkeypatch):
    from tests.test_cli import test_wp19_enrich_cmd as base
    runs = [{'run_id': 'saves-1', 'status': 'completed', 'changed': 3, 'undoable': False,
             'label': 'saves re-filed', 'created_at': '2026-10-12T12:00:00+00:00'},
            *base.STATUS['runs']]
    monkeypatch.setitem(base.STATUS, 'runs', runs)
    calls = fake_daemon(monkeypatch)
    interactive(monkeypatch, 'y')
    cmd_enrich(parse('undo'))
    assert [c[2]['run_id'] for c in calls if c[0] == 'POST'] == ['run-latest']


def test_status_marks_a_run_that_cannot_be_undone(monkeypatch, capsys):
    from tests.test_cli import test_wp19_enrich_cmd as base
    runs = [{'run_id': 'saves-1', 'status': 'completed', 'changed': 3, 'undoable': False,
             'label': 'saves re-filed', 'created_at': '2026-10-12T12:00:00+00:00'}]
    monkeypatch.setitem(base.STATUS, 'runs', runs)
    fake_daemon(monkeypatch)
    cmd_enrich(parse('status'))
    assert "saves-1" in capsys.readouterr().out
