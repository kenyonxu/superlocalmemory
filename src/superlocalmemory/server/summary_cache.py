"""Small, short-lived summary cache invalidated by store and WAL changes."""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from pathlib import Path

_cache = OrderedDict()
_lock = threading.Lock()
TTL_SECONDS = 30
MAX_ENTRIES = 32


def _revision(path):
    stamps = []
    for name in (str(path), str(path) + "-wal", str(Path(path).parent / "config.json")):
        try:
            info = Path(name).stat()
            stamps.append((info.st_ino, info.st_size, info.st_mtime_ns))
        except FileNotFoundError:
            stamps.append(None)
    return tuple(stamps)


def cached_daily(generator, path, day, profile, config, *, tz_offset_minutes):
    mode = getattr(getattr(config, "mode", None), "value", "a")
    llm = getattr(config, "llm", None)
    model = tuple(getattr(llm, name, "") for name in ("provider", "model", "api_base"))
    revision = _revision(path)
    key = (str(path), str(day), profile, mode, model, tz_offset_minutes, revision)
    now = time.monotonic()
    with _lock:
        item = _cache.get(key)
        if item and now - item[0] < TTL_SECONDS:
            _cache.move_to_end(key)
            return copy.deepcopy(item[1])
    result = generator(path, day, profile, config, tz_offset_minutes=tz_offset_minutes)
    if _revision(path) == revision and getattr(result, "coverage", "") != "unavailable":
        with _lock:
            _cache[key] = (time.monotonic(), copy.deepcopy(result))
            while len(_cache) > MAX_ENTRIES:
                _cache.popitem(last=False)
    return result
