"""Two-process proof: FULL engine host recovers embeddings via daemon fallback.

Process A (this test) owns the machine-wide embedding-worker singleton
(flock + live PID file). A stub daemon (uvicorn) serves /api/v3/embed*.
Process B semantics: an EmbeddingService in-process that loses the
singleton race and must fall back to the daemon over real HTTP.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI

import superlocalmemory.core.embeddings as embeddings_mod
from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.embeddings import (
    EmbeddingService,
    _embedding_pid_file,
    acquire_embedding_lock,
    release_embedding_lock,
)

_DIM = 384


def _make_app(hits: dict[str, int]) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v3/embed/ping")
    async def ping() -> dict:
        hits["ping"] += 1
        return {"ok": True}

    @app.post("/api/v3/embed")
    async def embed(body: dict) -> dict:
        hits["embed"] += 1
        texts = body.get("texts", [])
        return {"embeddings": [[0.01 * (i + 1)] * _DIM for i, _ in enumerate(texts)]}

    return app


@contextlib.contextmanager
def stub_daemon_port():
    """Serve the embed API from a real uvicorn server on an ephemeral port.

    Never binds 8765 (a production daemon may be live on this machine):
    port=0 lets the kernel assign a free port, read back from the live
    socket once the server reports ``started``.
    """
    hits: dict[str, int] = {"ping": 0, "embed": 0}
    config = uvicorn.Config(_make_app(hits), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):  # wait for bind
        if server.started and getattr(server, "servers", None):
            break
        time.sleep(0.1)
    if not (server.started and getattr(server, "servers", None)):
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("stub daemon failed to bind an ephemeral port")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield port, hits
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_fallback_recovers_embedding_over_real_http(monkeypatch, caplog, tmp_path) -> None:
    # All lock/PID state lives under a fresh tmp data root; the production
    # state dir (~/.superlocalmemory) and the live daemon on 8765 are never
    # touched. data_root reads SLM_DATA_DIR at call time, so this takes
    # effect before any lock helper below is called.
    data_dir = tmp_path / "slm-data"
    data_dir.mkdir()
    monkeypatch.setenv("SLM_DATA_DIR", str(data_dir))
    # Direct loopback HTTP: strip proxies so the round-trip to the stub
    # cannot be middle-boxed (this sandbox exports http_proxy/ALL_PROXY).
    for _var in (
        "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
        "all_proxy", "ALL_PROXY",
    ):
        monkeypatch.delenv(_var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    # Process A role: own the machine-wide singleton (flock + live PID file).
    assert acquire_embedding_lock(timeout=5.0), "test must own the embedding lock"
    pid_file = _embedding_pid_file()
    # The PID file MUST resolve under the fresh tmp dir — never the
    # production state dir.
    assert pid_file.parent == Path(os.path.realpath(data_dir)), pid_file
    original = pid_file.read_text() if pid_file.exists() else None
    pid_file.write_text(str(os.getpid()))
    try:
        with stub_daemon_port() as (port, hits):
            assert port not in (8765, 8767), "must never touch the production daemon"
            monkeypatch.setenv("SLM_DAEMON_PORT", str(port))
            svc = EmbeddingService(EmbeddingConfig(dimension=_DIM))
            try:
                with caplog.at_level(logging.WARNING):
                    # The very first request loses the singleton race, attaches
                    # the daemon fallback, and is served by the daemon in the
                    # SAME call — no silent first-call None degradation.
                    vec = svc.embed("hello world")
                assert vec is not None and len(vec) == _DIM
                # Exact stub payload: the vector provably came from the
                # daemon over HTTP, not from any local worker.
                assert vec == [0.01] * _DIM
                assert svc.embedder_mode == "daemon-fallback"
                assert svc.is_available is True
                assert svc.is_warm is True
                # Real HTTP round-trips happened: ping (attach) + embed.
                assert hits["ping"] >= 1 and hits["embed"] >= 1
                # Zero "returning None" warnings (regression §9.1 silent
                # missing-channel).
                assert not [r for r in caplog.records if "returning None" in r.getMessage()]
            finally:
                svc.shutdown()
    finally:
        release_embedding_lock()
        if original is None:
            pid_file.unlink(missing_ok=True)
        else:
            pid_file.write_text(original)

    # Machine state fully restored: the flock is released (re-acquirable)
    # and the PID file is back to its original content.
    assert embeddings_mod._embedding_lock_fd is None
    assert acquire_embedding_lock(timeout=2.0), "embedding lock must be free after the test"
    release_embedding_lock()
    restored = pid_file.read_text() if pid_file.exists() else None
    assert restored == original
