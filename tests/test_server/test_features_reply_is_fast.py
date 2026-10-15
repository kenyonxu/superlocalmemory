# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""``GET /api/v3/features`` must answer fast however large the installed media runtime is.

Measuring the runtime means walking tens of thousands of files, about a second
on a real install, which made ``slm doctor`` warn that the daemon "did not
answer". The reading is now taken in the background and served from memory.
"""

from __future__ import annotations

import threading
import time

import pytest

from superlocalmemory.server import media_size
from superlocalmemory.server.routes import features


@pytest.fixture(autouse=True)
def _fresh_cache():
    media_size.forget_all()
    yield
    media_size.forget_all()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "_ram_view", lambda: {})
    folder = tmp_path / "runtimes" / "media"
    folder.mkdir(parents=True)
    (folder / "model.bin").write_bytes(b"x" * 4096)
    return tmp_path


def _slow_walk(monkeypatch, seconds=0.6):
    started, release = threading.Event(), threading.Event()
    real = media_size.installed_bytes

    def slow(root):
        started.set()
        release.wait(seconds)
        return real(root)

    monkeypatch.setattr(media_size, "installed_bytes", slow)
    return started, release


def test_the_overview_does_not_wait_for_the_size_walk(runtime, monkeypatch):
    _, release = _slow_walk(monkeypatch)
    began = time.perf_counter()
    view = features._media_view({"enabled": True, "env": {"state": "ready"}}, runtime, fresh=False)
    assert time.perf_counter() - began < 0.3
    assert view["installed_bytes"] == 0  # unknown yet: the dashboard shows its estimate
    release.set()
    assert media_size.wait_idle(5)


def test_the_measurement_arrives_in_the_background_and_is_then_served_from_memory(
        runtime, monkeypatch):
    started, release = _slow_walk(monkeypatch)
    features._media_view({}, runtime, fresh=False)
    assert started.wait(2)
    release.set()
    assert media_size.wait_idle(5)
    began = time.perf_counter()
    view = features._media_view({}, runtime, fresh=False)
    assert view["installed_bytes"] == 4096 and time.perf_counter() - began < 0.1


def test_a_stale_reading_is_served_while_a_new_one_is_taken(runtime, monkeypatch):
    assert media_size.measure_now(runtime / "runtimes" / "media") == 4096
    (runtime / "runtimes" / "media" / "more.bin").write_bytes(b"y" * 100)
    monkeypatch.setattr(media_size, "CACHE_SECONDS", 0.0)
    assert media_size.cached_bytes(runtime / "runtimes" / "media") == 4096
    assert media_size.wait_idle(5)
    assert media_size.cached_bytes(runtime / "runtimes" / "media") == 4196


def test_an_absent_runtime_is_zero_without_a_walk(tmp_path, monkeypatch):
    def forbidden(root):
        raise AssertionError("nothing to walk")

    monkeypatch.setattr(media_size, "installed_bytes", forbidden)
    assert media_size.cached_bytes(tmp_path / "runtimes" / "media") == 0


def test_changing_the_feature_still_reports_the_measured_size(runtime):
    view = features._media_view({}, runtime)  # fresh by default, as enable/disable use it
    assert view["installed_bytes"] == 4096
