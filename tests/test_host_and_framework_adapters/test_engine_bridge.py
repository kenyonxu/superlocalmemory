"""Prove isolated adapters persist through the actual SLM engine, without TCP."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_bridge():
    path = ROOT / "ide/integrations/_engine_bridge.py"
    spec = importlib.util.spec_from_file_location("wp7_engine_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine_store(tmp_path):
    """Give the isolated child the same deterministic model deps as engine tests."""
    stores = []

    def open_store(bridge, module, cls, db):
        shim = tmp_path / "engine_store_fixture.py"
        shim.write_text(f"""import importlib.util
import sys
from unittest.mock import patch
sys.path.insert(0, {str(ROOT)!r})
from tests.conftest import mock_embedder
spec = importlib.util.spec_from_file_location("actual_adapter", {str(module)!r})
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
def make_store(db_path):
    with patch("superlocalmemory.core.engine_wiring.init_embedder",
               return_value=mock_embedder.__wrapped__()), patch(
               "superlocalmemory.core.engine_wiring.init_sufficiency_judge", return_value=None):
        return getattr(adapter, {cls!r})(db_path)
{cls} = make_store
""", encoding="utf-8")
        store = bridge.RemoteStore(sys.executable, shim, cls, db)
        stores.append(store)
        return store

    try:
        yield open_store
    finally:
        for store in stores:
            store._finalizer()
            store._reader.join(timeout=5)


@pytest.mark.parametrize(
    "name, package, filename, cls",
    [
        ("google-adk", "google_adk_superlocalmemory", "_v3_adk_store.py", "V3ADKStore"),
        ("crewai", "crewai_superlocalmemory", "_v3_crewai_store.py", "V3CrewAIStore"),
        (
            "semantic-kernel",
            "semantic_kernel_superlocalmemory",
            "_v3_record_store.py",
            "V3RecordStore",
        ),
    ],
)
def test_isolated_engine_persistence(tmp_path, engine_store, name, package, filename, cls):
    bridge = load_bridge()
    module = ROOT / "ide/integrations" / name / package / filename
    db = tmp_path / "memory.db"
    store = engine_store(bridge, module, cls, db)
    if name == "google-adk":
        store.add_events("app", "user", "session", [{"text": "Ada prefers tea"}])
        assert (
            store.list_events_for_session("app", "user", "session")[0]["event"]["text"]
            == "Ada prefers tea"
        )
    elif name == "crewai":
        store.save({"id": "r1", "content": "Ada prefers tea", "embedding": [1.0, 0.0]})
        assert store.get_record("r1")["content"] == "Ada prefers tea"
    else:
        store.upsert("docs", "d1", {"id": "d1", "text": "Ada prefers tea"})
        assert store.get("docs", "d1")["text"] == "Ada prefers tea"
    store.close()
    assert store._process.poll() == 0
    store.close()  # idempotent
    store = engine_store(bridge, module, cls, db)
    try:
        count = (
            len(store.list_events_for_session("app", "user", "session"))
            if name == "google-adk"
            else store.count()
            if name == "crewai"
            else len(store.list_records("docs"))
        )
        assert count == 1
    finally:
        store.close()


def test_missing_engine_fails_explicitly(tmp_path):
    bridge = load_bridge()
    with pytest.raises((OSError, RuntimeError)):
        bridge.RemoteStore("/nonexistent/slm-python", Path(__file__), "Store", tmp_path / "db")


class _DelayedReply:
    """Virtual completion latency; never sleep or start a model worker."""

    def __init__(self, latency):
        self.latency = latency

    def get(self, *, timeout):
        import queue

        if timeout < self.latency:
            raise queue.Empty
        return {"ok": True, "value": {"materialization_state": "complete"}}


def _pending_store(bridge, latency):
    import io
    import threading
    from types import SimpleNamespace

    store = object.__new__(bridge.RemoteStore)
    store._lock = threading.RLock()
    store._closed = False
    store._process = SimpleNamespace(stdin=io.StringIO())
    store._results = _DelayedReply(latency)
    disposed = []
    store._finalizer = lambda: disposed.append(True)
    return store, disposed


@pytest.mark.parametrize("method", ["add_events", "save", "upsert", "update", "create_collection"])
def test_synchronous_save_waits_past_the_short_rpc_budget(method):
    bridge = load_bridge()
    store, disposed = _pending_store(bridge, latency=61)

    assert store._call(method)["materialization_state"] == "complete"
    assert not store._closed
    assert disposed == []


@pytest.mark.parametrize("method, latency, budget", [("count", 61, 60), ("save", 301, 300)])
def test_bridge_waits_remain_bounded_and_reap_timed_out_workers(method, latency, budget):
    bridge = load_bridge()
    store, disposed = _pending_store(bridge, latency)

    with pytest.raises(TimeoutError, match=f"within {budget} seconds"):
        store._call(method)
    assert store._closed
    assert disposed  # the owned worker is stopped when its reply cannot arrive
