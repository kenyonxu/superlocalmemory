"""4.2.7: a mesh heartbeat that loses the race for memory.db is a 503 to retry."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from superlocalmemory.mesh import broker as broker_mod
from superlocalmemory.server.routes.mesh import _busy_is_retryable


def test_locked_database_becomes_503_with_retry_after() -> None:
    with pytest.raises(HTTPException) as caught:
        with _busy_is_retryable():
            raise sqlite3.OperationalError("database is locked")
    assert caught.value.status_code == 503
    assert caught.value.headers == {"Retry-After": "2"}


def test_other_database_errors_are_not_hidden() -> None:
    with pytest.raises(sqlite3.OperationalError):
        with _busy_is_retryable():
            raise sqlite3.OperationalError("no such table: mesh_peers")


def test_retry_budget_rides_out_a_longer_busy_moment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(broker_mod.time, "sleep", lambda _s: None)
    b = broker_mod.MeshBroker.__new__(broker_mod.MeshBroker)
    b._db_path = str(tmp_path / "m.db")
    calls = {"n": 0}

    def op(_conn):
        calls["n"] += 1
        if calls["n"] < 6:
            raise sqlite3.OperationalError("database is locked")
        return {"ok": True}

    assert b._write_with_retry(op) == {"ok": True}
    assert calls["n"] == 6
