"""A manual repair cannot outlive its owner or the subprocess sandbox."""

import threading

import pytest
from fastapi.testclient import TestClient

from tests.test_server.test_company_mode_mutation_routes import _nothing_leaves_the_sandbox


def test_manual_heal_is_owned_and_joined_before_subprocess_patch_teardown(
    engine_with_mock_deps, monkeypatch,
):
    from superlocalmemory.core import component_healer
    from superlocalmemory.server import unified_daemon
    from tests.thread_leak_guard import _is_product_thread

    entered, release, refused = threading.Event(), threading.Event(), threading.Event()
    workers = []
    def repair(*a, **k):
        import subprocess
        worker = threading.current_thread()
        workers.append(worker)
        entered.set()
        assert release.wait(5)
        try:
            subprocess.Popen(["sandbox-probe"])
        except RuntimeError:
            refused.set()
        return {}
    monkeypatch.setattr(component_healer, "heal_missing", repair)
    app = unified_daemon.create_app()
    app.state.engine = engine_with_mock_deps
    sandbox = pytest.MonkeyPatch()
    fixture = _nothing_leaves_the_sandbox.__wrapped__(sandbox)
    timer = threading.Timer(0.05, release.set)
    try:
        next(fixture)
        from superlocalmemory.core.security_primitives import ensure_install_token
        response = TestClient(app, client=("127.0.0.1", 50123)).post(
            "/api/v3/components/heal", headers={"X-Install-Token": ensure_install_token()})
        assert response.status_code == 200
        assert entered.wait(2)
        assert app.state.manual_heal_thread is workers[0]
        assert _is_product_thread(workers[0])
        timer.start()
        with pytest.raises(StopIteration):
            next(fixture)
        assert not workers[0].is_alive()
        assert refused.is_set(), "repair must finish under the original subprocess refusal"
    finally:
        release.set()
        if timer.ident is not None:
            timer.join(5)
        for worker in workers:
            worker.join(5)
        if fixture is not None:
            fixture.close()
        sandbox.undo()


def test_shutdown_joins_worker_and_closes_admission():
    from types import SimpleNamespace

    from superlocalmemory.server.manual_heal import join_manual_heals, start_manual_heal

    state = SimpleNamespace()
    entered, release = threading.Event(), threading.Event()
    def repair():
        entered.set()
        assert release.wait(5)
    timer = threading.Timer(0.05, release.set)
    try:
        assert start_manual_heal(state, repair)
        assert entered.wait(2)
        worker = state.manual_heal_thread
        assert start_manual_heal(state, repair)
        assert state.manual_heal_thread is worker
        assert not join_manual_heals(timeout_s=0.001)
        timer.start()
        assert join_manual_heals(state, timeout_s=None)
        assert not worker.is_alive()
        assert not start_manual_heal(state, repair)
    finally:
        release.set()
        if timer.ident is not None:
            timer.join(5)
        assert join_manual_heals(state)


def test_worker_start_failure_leaves_no_unjoinable_worker(monkeypatch):
    from types import SimpleNamespace

    from superlocalmemory.server.manual_heal import join_manual_heals, start_manual_heal

    state = SimpleNamespace()
    def refuse(*a, **k):
        raise RuntimeError("cannot start thread")
    monkeypatch.setattr(threading.Thread, "start", refuse)
    assert not start_manual_heal(state, lambda: None)
    assert state.manual_heal_thread is None
    assert join_manual_heals(state)
