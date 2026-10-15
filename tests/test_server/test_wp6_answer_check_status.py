from types import SimpleNamespace
from unittest.mock import Mock


def test_permanent_worker_failure_overrides_ready_install(monkeypatch):
    from superlocalmemory.core.config import RetrievalConfig
    from superlocalmemory.server.routes import answer_check_support as support

    monkeypatch.setattr(support, "effective_retrieval", lambda: RetrievalConfig())
    runtime = SimpleNamespace(to_dict=lambda: {"state": "ready", "action": ""})
    monkeypatch.setattr(support.laya_runtime, "detect", lambda _: runtime)
    judge = SimpleNamespace(
        backend="laya", _permanent_error="missing library", ready=False, loading=False
    )
    monkeypatch.setattr(support, "live_judge", lambda _: judge)
    monkeypatch.setattr(support, "active_backend", lambda *args: "laya")
    monkeypatch.setattr(support, "adopt_status", lambda: {})
    monkeypatch.setattr(support, "key_state", lambda _: {})
    monkeypatch.setattr(support, "read_tests", lambda: {})
    monkeypatch.setattr(
        support.laya_runtime.LayaInstallJob, "instance", lambda: Mock(running=False)
    )
    monkeypatch.setattr(support.laya_runtime.LayaTestJob, "instance", lambda: Mock(running=False))
    status = support.build_status(SimpleNamespace())
    assert status["laya"]["state"] != "ready"
    assert status["execution"]["state"] == "unavailable"
