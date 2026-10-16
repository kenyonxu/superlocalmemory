"""The power-user CLI uses the same actions as Memory health, in plain words."""

from argparse import ArgumentParser
from unittest.mock import Mock

import pytest

from superlocalmemory.cli.enrich_cmd import cmd_enrich, register_enrich_parser

STATUS = {
    'summary': 'Improving your memories: 3 of 12',
    'state': 'Waiting for your computer to be idle.',
    'enrichment': {'status': 'running', 'last_error': 'OperationalError'},
    'suggestions': [{'action': 'entity-links', 'found': 35, 'label': 'entity links'}],
    'runs': [
        {'run_id': 'run-undone', 'status': 'undone', 'changed': 4, 'label': 'search sketches',
         'created_at': '2026-10-12T11:00:00+00:00'},
        {'run_id': 'run-latest', 'status': 'completed', 'changed': 9,
         'label': 'memory descriptions', 'created_at': '2026-10-12T10:00:00+00:00'},
    ],
}


def parse(*argv):
    parser = ArgumentParser()
    register_enrich_parser(parser.add_subparsers(dest='command'))
    return parser.parse_args(['enrich', *argv])


def fake_daemon(monkeypatch, post=None):
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body, kwargs))
        if method == 'GET':
            return STATUS
        if isinstance(post, Exception):
            raise post
        return post or {'restored': 9, 'conflicts': 0}
    monkeypatch.setattr('superlocalmemory.cli.enrich_cmd.daemon_request', request)
    return calls


def interactive(monkeypatch, answer):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True, raising=False)
    monkeypatch.setattr('sys.stdout.isatty', lambda: True, raising=False)
    monkeypatch.setattr('builtins.input', lambda prompt: answer)


@pytest.mark.parametrize('action', ['pause', 'resume'])
def test_enrich_parser_and_actions(monkeypatch, action):
    request = Mock(return_value={'status': action})
    monkeypatch.setattr('superlocalmemory.cli.enrich_cmd.daemon_request', request)
    cmd_enrich(parse(action, 'synthetic-run', '--json'))
    request.assert_called_once_with(
        'POST', '/api/memory-health/action',
        {'action': action, 'run_id': 'synthetic-run'}, preserve_unprocessable=True)


def test_status_is_plain_language_with_run_ids(monkeypatch, capsys):
    fake_daemon(monkeypatch)
    cmd_enrich(parse('status'))
    out = capsys.readouterr().out
    assert 'Improving your memories: 3 of 12' in out
    assert 'Waiting for your computer to be idle.' in out
    assert 'run-latest' in out and 'run-undone' in out and '(undone)' in out
    assert 'slm enrich fix entity-links' in out
    assert 'OperationalError' not in out and '{' not in out


def test_undo_without_id_needs_yes_when_not_interactive(monkeypatch, capsys):
    calls = fake_daemon(monkeypatch)
    monkeypatch.setattr('sys.stdin.isatty', lambda: False, raising=False)
    with pytest.raises(SystemExit) as exited:
        cmd_enrich(parse('undo'))
    assert exited.value.code == 2
    assert '--yes' in capsys.readouterr().err
    assert not [c for c in calls if c[0] == 'POST']


def test_undo_without_id_targets_the_latest_changing_run(monkeypatch, capsys):
    calls = fake_daemon(monkeypatch)
    cmd_enrich(parse('undo', '--yes'))
    posts = [c for c in calls if c[0] == 'POST']
    assert posts[0][2] == {'action': 'undo', 'run_id': 'run-latest'}
    assert 'Undone: 9 changes restored.' in capsys.readouterr().out


def test_undo_asks_before_changing_anything(monkeypatch):
    calls = fake_daemon(monkeypatch)
    interactive(monkeypatch, 'n')
    with pytest.raises(SystemExit):
        cmd_enrich(parse('undo'))
    assert not [c for c in calls if c[0] == 'POST']
    interactive(monkeypatch, 'y')
    cmd_enrich(parse('undo'))
    assert [c[2]['run_id'] for c in calls if c[0] == 'POST'] == ['run-latest']


def test_a_refusal_prints_the_services_reason(monkeypatch, capsys):
    from superlocalmemory.cli.daemon import DaemonUnprocessable
    fake_daemon(monkeypatch, post=DaemonUnprocessable('', 'That run was already undone.'))
    with pytest.raises(SystemExit) as exited:
        cmd_enrich(parse('undo', 'run-latest', '--yes'))
    assert exited.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == 'That run was already undone.'
    assert 'Traceback' not in captured.err and '{' not in captured.out


def test_fix_is_explicit_and_confirmed(monkeypatch, capsys):
    calls = fake_daemon(monkeypatch, post={'found': 35, 'fixed': 35, 'run_id': 'fix-1'})
    cmd_enrich(parse('fix', 'entity-links', '--yes'))
    assert [c[2] for c in calls if c[0] == 'POST'] == [
        {'action': 'repair', 'repair': 'entity-links'}]
    assert 'slm enrich undo fix-1' in capsys.readouterr().out


def test_undo_prints_the_restored_total_of_a_native_repair(monkeypatch, capsys):
    native = {'restored': 27, 'conflicts': 0, 'facts_restored': 6, 'aliases_restored': 16,
              'junk_entities_restored': 5}
    fake_daemon(monkeypatch, post=native)
    cmd_enrich(parse('undo', 'fix-1', '--yes'))
    assert 'Undone: 27 changes restored.' in capsys.readouterr().out


def test_fix_include_undone_is_sent_only_when_asked(monkeypatch):
    calls = fake_daemon(monkeypatch, post={'found': 3, 'fixed': 3, 'run_id': 'fix-2'})
    cmd_enrich(parse('fix', 'entity-links', '--yes', '--include-undone'))
    cmd_enrich(parse('fix', 'refile-hidden', '--yes'))
    assert [c[2] for c in calls if c[0] == 'POST'] == [
        {'action': 'repair', 'repair': 'entity-links', 'include_undone': True},
        {'action': 'repair', 'repair': 'refile-hidden'}]
