"""Other processes embed through their own data root's daemon, with the right prompt.

A small local HTTP server stands in for the daemon. Nothing touches port 8765, and no
worker is started: a process that is not the daemon never loads the model.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.daemon_text_embedder import DaemonTextEmbedder
from superlocalmemory.core.mcp_embedder_proxy import McpEmbedderProxy
from superlocalmemory.infra.daemon_identity import build_descriptor, write_descriptor

MODEL = "google/embeddinggemma-2"
CONFIG = EmbeddingConfig(model_name=MODEL, dimension=4, provider="slm-media")


class FakeDaemon:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.embedder: dict | None = {"available": True, "warm": True, "model": MODEL, "dimension": 4}
        self.health: dict = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _reply(self, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    self._reply(outer.health)
                else:
                    ping = {"ok": True}
                    if outer.embedder is not None:
                        ping["embedder"] = outer.embedder
                    self._reply(ping)

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.posts.append(body)
                width = 4
                self._reply({"embeddings": [[0.25] * width for _ in body.get("texts", [])]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="fake-daemon")
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture()
def daemon():
    d = FakeDaemon()
    root = os.environ["SLM_DATA_DIR"]
    descriptor = build_descriptor(data_root=root, port=d.port, version="test", state="ready")
    write_descriptor(descriptor, data_root=root)
    d.health = descriptor.public_health_fields()
    yield d
    d.close()


def test_documents_and_questions_ask_the_daemon_with_their_prompt(daemon):
    emb = DaemonTextEmbedder(CONFIG)
    assert emb.embed("a memory") == [0.25] * 4
    assert emb.embed_batch(["a", "b"]) == [[0.25] * 4, [0.25] * 4]
    assert emb.embed_query("what did I save?") == [0.25] * 4
    assert [(p["texts"], p.get("prompt")) for p in daemon.posts] == [
        (["a memory"], "document"), (["a", "b"], "document"), (["what did I save?"], "query")]


def test_available_only_when_the_daemons_embedder_is_this_space(daemon):
    assert DaemonTextEmbedder(CONFIG).is_available is True
    daemon.embedder = {"available": True, "warm": True, "model": "nomic-ai/nomic-embed-text-v1.5", "dimension": 4}
    assert DaemonTextEmbedder(CONFIG).is_available is False        # the wrong space is never used
    daemon.embedder = {"available": True, "warm": True, "model": MODEL, "dimension": 768}
    assert DaemonTextEmbedder(CONFIG).is_available is False
    daemon.embedder = {"available": False, "warm": False, "model": MODEL, "dimension": 4}
    assert DaemonTextEmbedder(CONFIG).is_available is False
    daemon.embedder = None                                            # an older daemon that says nothing
    assert DaemonTextEmbedder(CONFIG).is_available is False


def test_warmth_is_the_daemons(daemon):
    daemon.embedder = {"available": True, "warm": False, "model": MODEL, "dimension": 4}
    emb = DaemonTextEmbedder(CONFIG, ping_ttl_s=0.0)
    assert emb.is_available and emb.is_warm is False and emb.has_loaded_once is False
    daemon.embedder = {"available": True, "warm": True, "model": MODEL, "dimension": 4}
    assert emb.is_warm is True and emb.has_loaded_once is True


def test_no_daemon_means_unavailable_and_no_vectors_ever():
    emb = DaemonTextEmbedder(CONFIG)        # no descriptor for this data root: nothing to talk to
    assert emb.is_available is False and emb._available is False and emb.is_warm is False
    assert emb.embed("x") is None
    assert emb.embed_batch(["a", "b"]) == [None, None]
    assert emb.embed_query("q") is None


def test_a_vector_of_the_wrong_width_is_refused(daemon):
    from superlocalmemory.core.embeddings import DimensionMismatchError

    daemon.embedder = {"available": True, "warm": True, "model": MODEL, "dimension": 8}  # says 8, sends 4
    emb = DaemonTextEmbedder(EmbeddingConfig(model_name=MODEL, dimension=8, provider="slm-media"))
    with pytest.raises(DimensionMismatchError):
        emb.embed("x")


def test_empty_input_is_refused_and_fisher_params_are_local(daemon):
    from superlocalmemory.core.embeddings import EmbeddingService

    emb = DaemonTextEmbedder(CONFIG)
    with pytest.raises(ValueError):
        emb.embed(" ")
    vec = [0.1, -0.4, 0.2, 0.7]
    assert emb.compute_fisher_params(vec) == EmbeddingService(EmbeddingConfig(dimension=4)).compute_fisher_params(vec)


def test_the_mcp_proxy_can_ask_questions_too(daemon):
    proxy = McpEmbedderProxy()
    assert proxy.embed_query("a question") == [0.25] * 4
    assert proxy.embed_batch(["a"]) == [[0.25] * 4]
    assert daemon.posts[0] == {"texts": ["a question"], "prompt": "query"}
    assert "prompt" not in daemon.posts[1]       # the request an existing daemon gets is unchanged


def test_full_python_api_routes_to_same_root_daemon(daemon, monkeypatch):
    """ENG-10: a full API process delegates without starting a model worker."""
    from types import SimpleNamespace

    from superlocalmemory.core import process_role, text_provider

    monkeypatch.setattr(process_role, '_daemon', False)
    emb = text_provider.init_slm_media_embedder(SimpleNamespace(embedding=CONFIG))
    assert isinstance(emb, DaemonTextEmbedder)
    assert emb.embed_query('Find the incident decision') == [0.25] * 4
    assert daemon.posts[-1]['prompt'] == 'query'


def test_full_legacy_provider_reuses_compatible_daemon(daemon, monkeypatch):
    from types import SimpleNamespace

    from superlocalmemory.core import process_role, text_provider

    monkeypatch.setattr(process_role, '_daemon', False)
    legacy = EmbeddingConfig(model_name=MODEL, dimension=4, provider='sentence-transformers')
    def forbidden_worker(_config):
        raise AssertionError('A second worker must not be started')
    emb = text_provider.route_embedder(SimpleNamespace(embedding=legacy),
                                      try_ollama=forbidden_worker,
                                      try_service=forbidden_worker)
    assert isinstance(emb, DaemonTextEmbedder)
    assert emb.embed_query('Find the recovery approval') == [0.25] * 4


@pytest.mark.parametrize('wrong_space', [False, True])
def test_legacy_provider_falls_back_when_daemon_not_compatible(daemon, monkeypatch, wrong_space):
    from types import SimpleNamespace

    from superlocalmemory.core import process_role, text_provider

    monkeypatch.setattr(process_role, '_daemon', False)
    daemon.embedder = ({'available': True, 'warm': True, 'model': 'other', 'dimension': 4}
                       if wrong_space else None)
    legacy = EmbeddingConfig(model_name=MODEL, dimension=4, provider='sentence-transformers')
    local = object()
    assert text_provider.route_embedder(SimpleNamespace(embedding=legacy),
                                        try_ollama=lambda _: None,
                                        try_service=lambda _: local) is local


def test_legacy_provider_never_sends_another_roots_text_to_the_daemon(
    daemon, monkeypatch, tmp_path,
):
    from types import SimpleNamespace

    from superlocalmemory.core import process_role, text_provider

    monkeypatch.setattr(process_role, '_daemon', False)
    legacy = EmbeddingConfig(model_name=MODEL, dimension=4, provider='sentence-transformers')
    local = object()
    emb = text_provider.route_embedder(
        SimpleNamespace(embedding=legacy, base_dir=tmp_path / 'other'),
        try_ollama=lambda _: None, try_service=lambda _: local)
    assert emb is local
    assert daemon.posts == []


def test_legacy_api_keeps_the_compatible_proxy_while_daemon_warms(daemon, monkeypatch):
    from types import SimpleNamespace

    from superlocalmemory.core import process_role, text_provider

    monkeypatch.setattr(process_role, '_daemon', False)
    daemon.embedder = {'available': False, 'warm': False, 'model': MODEL, 'dimension': 4}
    local = object()
    legacy = EmbeddingConfig(model_name=MODEL, dimension=4, provider='sentence-transformers')
    emb = text_provider.route_embedder(SimpleNamespace(embedding=legacy),
                                       try_ollama=lambda _: None, try_service=lambda _: local)
    assert isinstance(emb, DaemonTextEmbedder)
    assert emb.is_available is False
    daemon.embedder = {'available': True, 'warm': True, 'model': MODEL, 'dimension': 4}
    emb._ping_at = float('-inf')
    assert emb.embed_query('Recover after warmup') == [0.25] * 4
