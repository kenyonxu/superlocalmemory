"""Local JSON pipe to SLM's interpreter, bundled into incompatible adapters.

This is a process boundary, not a second storage implementation. The worker
loads the adapter's existing framework-free store and uses the canonical SLM
engine/schema. No TCP listener, shell, pickle, or framework dependency enters
SLM's interpreter. SLM_ENGINE_PYTHON must name an explicitly installed engine.
"""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import weakref
from contextlib import redirect_stdout
from pathlib import Path

_MAX_FRAME = 16 * 1024 * 1024
_TIMEOUT = 60
# Canonical saves complete enrichment, matching the CLI synchronous budget.
_SAVE_TIMEOUT = 300
_SAVE_METHODS = frozenset({"add_events", "save", "update", "upsert", "create_collection"})


def _dispose(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()


def _read_frames(stream, results):
    try:
        while True:
            line = stream.readline(_MAX_FRAME + 1)
            if not line:
                results.put(RuntimeError("SLM engine exited before replying"))
                return
            if len(line.encode("utf-8")) > _MAX_FRAME or not line.endswith("\n"):
                results.put(RuntimeError("SLM engine reply exceeded the frame limit"))
                return
            results.put(json.loads(line))
    except Exception as exc:
        results.put(RuntimeError(f"SLM engine protocol failure: {type(exc).__name__}"))


class RemoteStore:
    """Serialize one store's calls; bound waits and reap every owned child."""

    def __init__(self, python, module_path, class_name, db_path):
        env = {k: v for k, v in os.environ.items() if k != "SLM_ENGINE_PYTHON"}
        self._lock = threading.RLock()
        self._results = queue.Queue()
        self._closed = False
        self._process = subprocess.Popen(
            [
                str(python),
                "-I",
                str(Path(__file__).resolve()),
                "--worker",
                str(Path(module_path).resolve()),
                class_name,
                str(Path(db_path).expanduser().resolve()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self._finalizer = weakref.finalize(self, _dispose, self._process)
        self._reader = threading.Thread(
            target=_read_frames, args=(self._process.stdout, self._results), daemon=True
        )
        self._reader.start()
        try:
            self._receive()
        except Exception:
            self._finalizer()
            raise

    def _receive(self, timeout=_TIMEOUT):
        try:
            result = self._results.get(timeout=timeout)
        except queue.Empty as exc:
            self._finalizer()
            raise TimeoutError(f"SLM engine did not reply within {timeout} seconds") from exc
        if isinstance(result, Exception):
            raise result
        if not result.get("ok"):
            error_type = ValueError if result.get("type") == "ValueError" else RuntimeError
            raise error_type(result.get("error", "SLM engine call failed"))
        return result.get("value")

    def _call(self, name, *args, **kwargs):
        with self._lock:
            if self._closed:
                raise RuntimeError("SLM engine store is closed")
            frame = json.dumps({"method": name, "args": args, "kwargs": kwargs}) + "\n"
            if len(frame.encode("utf-8")) > _MAX_FRAME:
                raise ValueError("SLM engine request exceeds the frame limit")
            try:
                self._process.stdin.write(frame)
                self._process.stdin.flush()
                timeout = _SAVE_TIMEOUT if name in _SAVE_METHODS else _TIMEOUT
                return self._receive(timeout=timeout)
            except (BrokenPipeError, OSError, TimeoutError):
                self._closed = True
                self._finalizer()
                raise

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self._call(name, *args, **kwargs)

    def close(self):
        with self._lock:
            if self._closed:
                return
            try:
                self._call("close")
                self._process.wait(timeout=5)
            finally:
                self._closed = True
                self._finalizer()
                self._reader.join(timeout=5)


def _reply(stream, value):
    encoded = json.dumps(value, ensure_ascii=True) + "\n"
    if len(encoded) > _MAX_FRAME:
        encoded = json.dumps({"ok": False, "error": "SLM engine result exceeds frame limit"}) + "\n"
    stream.write(encoded)
    stream.flush()


def _load_store(module_path, class_name, db_path):
    spec = importlib.util.spec_from_file_location("_slm_framework_store", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)(db_path)


def _invoke(store, request):
    method = request["method"]
    if not isinstance(method, str) or method.startswith("_"):
        raise ValueError("Only public store methods are callable")
    return getattr(store, method)(*request["args"], **request["kwargs"])


def _worker(module_path, class_name, db_path):
    protocol = sys.stdout
    store = None
    try:
        with redirect_stdout(sys.stderr):
            store = _load_store(module_path, class_name, db_path)
        _reply(protocol, {"ok": True})
        for line in sys.stdin:
            if len(line.encode("utf-8")) > _MAX_FRAME:
                raise ValueError("SLM engine request exceeds frame limit")
            request = json.loads(line)
            try:
                with redirect_stdout(sys.stderr):
                    value = _invoke(store, request)
                _reply(protocol, {"ok": True, "value": value})
            except Exception as exc:
                _reply(protocol, {"ok": False, "error": str(exc), "type": type(exc).__name__})
            if request.get("method") == "close":
                return
    except Exception as exc:
        _reply(protocol, {"ok": False, "error": str(exc), "type": type(exc).__name__})
    finally:
        if store is not None:
            with redirect_stdout(sys.stderr):
                store.close()


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--worker":
        raise SystemExit("Internal SLM adapter worker; configure SLM_ENGINE_PYTHON instead")
    _worker(*sys.argv[2:])
