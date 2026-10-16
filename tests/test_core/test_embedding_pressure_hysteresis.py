"""E-M3: pressure cannot repeatedly unload and reload the same model."""

from types import SimpleNamespace

import psutil
import pytest

from superlocalmemory.core import embeddings
from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.infra import proc_memory
from tests.helpers.fake_embedding_worker import Spawner, answering_worker

GB = 1024**3


@pytest.fixture
def lane(monkeypatch):
    memory = [3.0]
    spawner = Spawner(answering_worker(), on_spawn=lambda: memory.__setitem__(0, 1.2))
    monkeypatch.setattr(embeddings, "subprocess", spawner.namespace())
    monkeypatch.setenv("SLM_MIN_AVAILABLE_MEMORY_GB", "2.0")
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(
        free=memory[0] * GB, inactive=0, available=memory[0] * GB))
    monkeypatch.setattr(proc_memory, "process_memory_mb", lambda _pid: 1400.0)
    service = embeddings.EmbeddingService(EmbeddingConfig(dimension=4))
    try:
        yield service, spawner, memory
    finally:
        service.shutdown()
        spawner.reap()


def test_resident_footprint_does_not_pay_the_cold_load_floor_again(lane):
    service, spawner, _memory = lane
    for _ in range(8):
        assert service.embed("a request") == [0.5] * 4
    assert service.is_warm
    assert len(spawner.procs) == 1


def test_pressure_stop_survives_heal_requests_and_reports_reason(lane):
    service, spawner, memory = lane
    assert service.embed("warm") == [0.5] * 4
    memory[0] = 0.2
    assert service.embed("pressure") == [0.5] * 4
    assert not service.is_warm
    memory[0] = 2.1  # above old 2 GB floor, below the recovery headroom
    for _ in range(8):
        service._available = None  # recall-health's availability reset
        assert service.embed("still blocked") is None
    assert len(spawner.procs) == 1
    status = service.memory_status()
    assert status["state"] == "memory_pressure"
    assert "memory" in status["reason"].lower()
    assert status["footprint_mb"] == 1400.0
    assert status["retry_in_s"] > 0


def test_recovery_requires_cooldown_and_headroom(lane, monkeypatch):
    from superlocalmemory.core import embedding_memory

    now = [100.0]
    monkeypatch.setattr(embedding_memory, "monotonic", lambda: now[0])
    service, spawner, memory = lane
    service.embed("warm")
    memory[0] = 0.2
    service.embed("pressure")
    memory[0] = 4.0
    assert service.embed("too soon") is None
    now[0] += 61.0
    memory[0] = 2.1
    assert service.embed("too little headroom") is None
    memory[0] = 4.0
    assert service.embed("recovered") == [0.5] * 4
    assert service.is_warm and len(spawner.procs) == 2
    assert service.memory_status()["reason"] is None


def test_status_reads_the_guard_owned_since_construction(lane):
    from superlocalmemory.core.embedding_memory import guard_for

    service, _spawner, _memory = lane
    original = service.__dict__["_pressure_pause"]
    original.block()
    for _ in range(50):
        assert service.memory_status()["state"] == "memory_pressure"
        assert guard_for(service) is original


def test_unknown_footprint_keeps_full_load_floor(lane, monkeypatch):
    from superlocalmemory.core.embedding_memory import enough_memory

    _service, _spawner, memory = lane
    memory[0] = 1.2
    monkeypatch.setattr(proc_memory, "process_memory_mb", lambda _pid: 0.0)
    assert not enough_memory(worker_pid=1234)


def test_unreadable_headroom_lets_a_paused_worker_start_like_the_load_check(lane, monkeypatch):
    from superlocalmemory.core import embedding_memory

    service, _spawner, _memory = lane
    guard = service._pressure_pause
    guard.block()
    monkeypatch.setattr(embedding_memory, "monotonic", lambda: guard.retry_at + 1)
    monkeypatch.setattr(embedding_memory, "available_mb", lambda: None)
    assert embedding_memory.enough_memory() is True
    assert guard.may_start()
    assert guard.snapshot()["state"] == "ready"


def test_footprint_follows_the_latest_measurement_not_a_lifetime_maximum(monkeypatch):
    from superlocalmemory.core.embedding_memory import PressurePause

    guard = PressurePause()
    sizes = iter([3000.0, 1400.0, 0.0])
    monkeypatch.setattr(proc_memory, "process_memory_mb", lambda _pid: next(sizes))
    guard.block(1)
    assert guard.footprint == 3000.0
    guard.block(1)
    assert guard.footprint == 1400.0  # a smaller model replaced the large one
    guard.block(1)
    assert guard.footprint == 1400.0  # unmeasurable (worker gone): keep the last reading


def test_the_pause_message_says_how_much_is_needed_and_when_it_retries(lane, monkeypatch):
    from superlocalmemory.core import embedding_memory

    service, _spawner, memory = lane
    guard = service._pressure_pause
    memory[0] = 0.2
    guard.block()
    memory[0] = 1.5  # moved on since the pause began
    snap = guard.snapshot()
    text = snap["reason"]
    assert f"{guard.recovery_mb:.0f} MB" in text
    assert "1536 MB" in text  # the fresh reading, not the one taken at the pause
    assert "60 s" in text
    assert snap["available_mb"] == pytest.approx(1536.0)
    monkeypatch.setattr(embedding_memory, "monotonic", lambda: guard.retry_at + 5)
    assert "again" in guard.snapshot()["reason"]
