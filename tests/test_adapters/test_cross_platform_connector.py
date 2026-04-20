"""Tests for CrossPlatformConnector orchestration (LLD-05 §8.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from superlocalmemory.hooks.ide_connector import (
    AdapterStatus,
    CrossPlatformConnector,
)


class _FakeAdapter:
    def __init__(self, name: str, *, active=True, wrote=True,
                 raise_detect=False, raise_sync=False, target="/tmp/x"):
        self.name = name
        self._active = active
        self._wrote = wrote
        self._raise_detect = raise_detect
        self._raise_sync = raise_sync
        self.target_path = Path(target)
        self.disabled = False

    def is_active(self):
        if self._raise_detect:
            raise RuntimeError("detect fail")
        return self._active

    def sync(self):
        if self._raise_sync:
            raise RuntimeError("sync fail")
        return self._wrote

    def disable(self):
        self.disabled = True


def test_detect_reports_status_for_each_adapter():
    a = _FakeAdapter("a", active=True)
    b = _FakeAdapter("b", active=False)
    conn = CrossPlatformConnector([a, b])
    statuses = conn.detect()
    names = [s.name for s in statuses]
    assert names == ["a", "b"]
    assert statuses[0].active is True
    assert statuses[1].active is False


def test_detect_handles_exception():
    a = _FakeAdapter("a", raise_detect=True)
    conn = CrossPlatformConnector([a])
    statuses = conn.detect()
    assert statuses[0].active is False


def test_connect_skips_inactive():
    a = _FakeAdapter("a", active=False)
    b = _FakeAdapter("b", active=True, wrote=True)
    conn = CrossPlatformConnector([a, b])
    results = conn.connect()
    assert results["a"] == "inactive"
    assert results["b"] == "wrote"


def test_connect_returns_skipped_on_no_write():
    a = _FakeAdapter("a", active=True, wrote=False)
    conn = CrossPlatformConnector([a])
    results = conn.connect()
    assert results["a"] == "skipped"


def test_connect_swallows_exceptions():
    a = _FakeAdapter("a", active=True, raise_sync=True)
    conn = CrossPlatformConnector([a])
    results = conn.connect()
    assert results["a"].startswith("error:")


def test_disable_flips_adapter():
    a = _FakeAdapter("a")
    conn = CrossPlatformConnector([a])
    assert conn.disable("a") is True
    assert a.disabled is True


def test_disable_unknown_adapter_returns_false():
    conn = CrossPlatformConnector([_FakeAdapter("a")])
    assert conn.disable("does_not_exist") is False


def test_adapters_property_returns_copy():
    a = _FakeAdapter("a")
    conn = CrossPlatformConnector([a])
    out = conn.adapters
    out.clear()
    # Internal list unchanged.
    assert len(conn.adapters) == 1
