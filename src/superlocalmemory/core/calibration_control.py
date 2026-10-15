# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Cancellation-aware local inference and idle waits."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager, nullcontext


class CancelToken:
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._clients = set()

    def bind(self, engine, current_engine=None, lease=None, progress=None):
        self._engine = engine
        self._current_engine = current_engine or (lambda: engine)
        self._lease = lease or (lambda: nullcontext(True))
        self._progress = progress
        from superlocalmemory.storage.embedding_spaces import signature_of

        self._snapshot = (signature_of(engine._config.embedding), engine.profile_id)
        self._judge = engine._retrieval_engine._sufficiency_judge

    @contextmanager
    def operation(self):
        with self._lease() as admitted:
            if admitted is None or self.is_set() or self._current_engine() is not self._engine:
                self.set()
                raise ValueError("Engine changed or calibration cancelled; nothing replaced")
            yield

    def is_set(self):
        if self._event.is_set():
            return True
        if not hasattr(self, "_current_engine"):
            return False
        from superlocalmemory.storage.embedding_spaces import signature_of

        engine = self._engine
        return (
            self._current_engine() is not engine
            or (signature_of(engine._config.embedding), engine.profile_id) != self._snapshot
            or engine._retrieval_engine._sufficiency_judge is not self._judge
        )

    @property
    def cancel_requested(self):
        return self._event.is_set()

    def report(self, **values):
        if self._progress is not None:
            self._progress(**values)

    def wait(self, timeout):
        return self._event.wait(timeout)

    def set(self):
        self._event.set()
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                if client.sock is not None:
                    client.sock.shutdown(socket.SHUT_RDWR)
                client.close()
            except OSError:
                pass

    @contextmanager
    def client(self, connection):
        with self._lock:
            if self.is_set():
                raise ValueError("Calibration cancelled")
            self._clients.add(connection)
        try:
            yield connection
        finally:
            connection.close()
            with self._lock:
                self._clients.discard(connection)


def await_idle(stop):
    from superlocalmemory.core import recall_gate

    deadline = time.monotonic() + 60
    while not stop.is_set():
        with recall_gate.idle_wait_deadline(time.monotonic() + 0.1):
            recall_gate.wait_for_foreground_idle()
        if recall_gate.in_flight() == 0:
            return
        if time.monotonic() >= deadline:
            raise ValueError("Calibration paused too long behind live recalls; retry later")
        stop.wait(0.01)
    raise ValueError("Calibration cancelled")


def require_local(engine):
    from urllib.parse import urlsplit

    embedding = getattr(engine._embedder, "_config", engine._config.embedding)
    provider = getattr(embedding, "provider", "")
    if provider not in {"", "sentence-transformers", "ollama", "slm-media"}:
        raise ValueError("Calibration requires a local embedding provider")
    endpoint = getattr(engine._embedder, "_base_url", "")
    if not endpoint:
        endpoint = (
            getattr(embedding, "ollama_base_url", "")
            if provider == "ollama"
            else getattr(embedding, "api_endpoint", "")
        )
    if endpoint and urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Calibration requires a local embedding endpoint")
    retrieval = engine._config.retrieval
    from superlocalmemory.retrieval.remote_reranker_config import is_remote_cross_encoder_backend

    if is_remote_cross_encoder_backend(getattr(retrieval, "cross_encoder_backend", "")):
        address = getattr(retrieval, "cross_encoder_endpoint", "")
        if urlsplit(address).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Calibration requires a local reranker")
