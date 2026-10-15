"""Process ownership persists through reparenting but never through PID reuse."""

import os
import sys
from types import SimpleNamespace

import psutil
import pytest

from superlocalmemory.loops import check_process, process_sweep


def process(pid, *, parent=1, environment=None):
    def environ():
        if environment is None:
            raise psutil.AccessDenied(pid)
        return environment

    return SimpleNamespace(pid=pid, ppid=lambda: parent, environ=environ)


@pytest.mark.parametrize('link', ['ancestry', 'session', 'group', 'token'])
def test_each_ownership_link_survives_reparenting_and_session_change(monkeypatch, link):
    owner = process_sweep.CheckProcesses(100, 10., 'private-run', {})
    child = process(101, parent=100 if link == 'ancestry' else 1,
                    environment={'SLM_LOOP_RUN_TOKEN': 'private-run'} if link == 'token'
                    else None)
    root = process(100)
    monkeypatch.setattr(os, 'getsid', lambda pid: 100 if link == 'session' else pid)
    monkeypatch.setattr(os, 'getpgid', lambda pid: 100 if link == 'group' else pid)
    current = {(100, 10.): (root, 'root'), (101, 11.): (child, 'child')}
    assert (child, 'child') in owner.owned(current)
    monkeypatch.setattr(os, 'getsid', lambda pid: pid)
    monkeypatch.setattr(os, 'getpgid', lambda pid: pid)
    reparented = process(101)
    assert owner.owned({(101, 11.): (reparented, 'child')}) == [(reparented, 'child')]
    reused = process(101)
    assert owner.owned({(101, 12.): (reused, 'unrelated')}) == []


def test_parent_chain_is_order_independent_and_ignores_baseline_processes(monkeypatch):
    baseline = process(103, environment={'SLM_LOOP_RUN_TOKEN': 'private-run'})
    owner = process_sweep.CheckProcesses(100, 10., 'private-run', {(103, 9.): baseline})
    monkeypatch.setattr(os, 'getsid', lambda pid: pid)
    monkeypatch.setattr(os, 'getpgid', lambda pid: pid)
    root, child, grandchild = process(100), process(101, parent=100), process(102, parent=101)
    current = {(102, 12.): (grandchild, 'grandchild'), (101, 11.): (child, 'child'),
               (100, 10.): (root, 'root'), (103, 9.): (baseline, 'baseline')}
    owned = owner.owned(current)
    assert (grandchild, 'grandchild') in owned
    assert (baseline, 'baseline') not in owned


def test_unreadable_environment_and_ids_do_not_attribute_unrelated_process(monkeypatch):
    owner = process_sweep.CheckProcesses(100, 10., 'private-run', {})

    def denied(pid):
        raise PermissionError('process IDs are unavailable')

    monkeypatch.setattr(os, 'getsid', denied)
    monkeypatch.setattr(os, 'getpgid', denied)
    unrelated = process(101)
    assert owner.owned({(101, 11.): (unrelated, 'unrelated')}) == []


def test_execution_injects_fresh_token_without_inheriting_parent_token(tmp_path, monkeypatch):
    monkeypatch.setenv('SLM_LOOP_RUN_TOKEN', 'parent-token')
    command = [sys.executable, '-I', '-c',
               'import hashlib,os; '
               'token=os.environ["SLM_LOOP_RUN_TOKEN"]; '
               'assert len(token)==64; '
               'print(hashlib.sha256(token.encode()).hexdigest())']
    first = check_process.execute(command, tmp_path, 3)
    second = check_process.execute(command, tmp_path, 3)
    assert first[0] == second[0] == 0
    assert first.reason is second.reason is None
    assert first[1] != second[1]
