# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Bounded reuse of the original temporal SQL slice, with source-write currency."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Any, Callable


def _version(db: Any, near_date: str | None) -> tuple | None:
    txn = getattr(db, "_txn_state", None)
    if txn is not None and (getattr(txn, "conn", None) is not None
                            or getattr(txn, "coordinator_bound", False)):
        return None
    if near_date is not None and str(near_date).lower() in {"now", "subsec", "subsecond"}:
        return None
    try:
        stat = os.stat(db.db_path)
        rows = db.execute("SELECT version,uncacheable FROM temporal_retrieval_version WHERE id=1")
        row = dict(rows[0])
        if row["uncacheable"]:
            return None
        return stat.st_dev, stat.st_ino, int(row["version"])
    except (AttributeError, KeyError, IndexError, OSError, TypeError, ValueError):
        return None
    except Exception:  # legacy schema: original SQL remains the authoritative answer
        return None


class TemporalSliceCache:
    """Eight visibility/date slices, at most 40,000 narrow rows per channel."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._epoch: tuple | None = None
        self._rows: OrderedDict[tuple, tuple[dict, ...]] = OrderedDict()

    def load(self, db: Any, key: tuple, read: Callable[[], list[dict]]) -> list[dict]:
        epoch = _version(db, key[-1])
        if epoch is None:
            return read()
        with self._lock:
            if epoch != self._epoch:
                self._rows.clear()
                self._epoch = epoch
            hit = self._rows.get(key)
            if hit is not None:
                self._rows.move_to_end(key)
                return [dict(row) for row in hit]
        rows = read()
        with self._lock:
            if self._epoch == epoch:
                self._rows[key] = tuple(dict(row) for row in rows)
                self._rows.move_to_end(key)
                while len(self._rows) > 8:
                    self._rows.popitem(last=False)
        return rows
