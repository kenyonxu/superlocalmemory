"""WP8 CLI failures must be observable to automation."""
from argparse import Namespace

import pytest

from superlocalmemory.cli import commands, daemon, embedder_cmd


def test_session_open_failure_exits_nonzero(monkeypatch):
    monkeypatch.setattr(daemon, 'is_daemon_running', lambda: True)
    monkeypatch.setattr(daemon, 'daemon_request', lambda *a, **kw: None)
    with pytest.raises(SystemExit) as error:
        commands.cmd_session(Namespace(session_command='open'))
    assert error.value.code == 1


def test_sync_remember_refuses_queryable_receipt(monkeypatch):
    monkeypatch.setattr(daemon, 'is_daemon_running', lambda: True)
    monkeypatch.setattr(daemon, 'daemon_request', lambda *a, **kw: {
        'fact_ids': ['f'], 'operation_id': 'op', 'materialization_state': 'queryable'})
    with pytest.raises(SystemExit) as error:
        commands.cmd_remember(Namespace(content='Keep the incident decision', tags='',
                                        sync_mode=True, json=True))
    assert error.value.code == 1


def test_queued_embedder_explains_wait():
    job = dict(job_id=1, kind='upgrade', state='queued', **{'from': 'a', 'to': 'b'},
               done=0, total=5)
    assert 'Reason:' in embedder_cmd.describe(job)


def test_completion_timeout_preserves_operation_id(monkeypatch):
    import asyncio
    import time

    from fastapi import HTTPException

    from superlocalmemory.server import remember_completion

    def slow(*args):
        time.sleep(0.02)
        return {}
    monkeypatch.setattr(remember_completion, 'complete_remember', slow)
    with pytest.raises(HTTPException) as error:
        asyncio.run(remember_completion.wait_for_completion(
            None, {'operation_id': 'saved-op'}, 'default', timeout_seconds=0.001))
    assert error.value.status_code == 503
    assert error.value.detail['operation_id'] == 'saved-op'
    assert error.value.detail['code'] == 'MATERIALIZATION_TIMEOUT'


def test_sync_server_failure_is_reported_as_incomplete(monkeypatch, capsys):
    monkeypatch.setattr(daemon, 'is_daemon_running', lambda: True)
    def refused(*args, **kwargs):
        assert kwargs['preserve_server_error'] is True
        raise daemon.DaemonServerError(503, 'MATERIALIZATION_INCOMPLETE', 'Saved; retry completion')
    monkeypatch.setattr(daemon, 'daemon_request', refused)
    with pytest.raises(SystemExit) as error:
        commands.cmd_remember(Namespace(content='Keep the approved decision', tags='',
                                        sync_mode=True, json=True))
    assert error.value.code == 1
    assert 'MATERIALIZATION_INCOMPLETE' in capsys.readouterr().out
