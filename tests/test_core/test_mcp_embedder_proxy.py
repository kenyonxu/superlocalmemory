# tests/test_core/test_mcp_embedder_proxy.py
"""Tests for McpEmbedderProxy strict mode and timeout configurability.

Merge 4.2.6 (outbound gate): the direct-port transport goes through
``core.outbound_http`` — the ping via ``outbound_http.urlopen``, the embed
POST via ``outbound_http.post_json`` (httpx under the gate, so strict mode
still re-raises httpx errors). The seams below patch the gate entry points;
tests/test_integration/test_embedding_fallback_two_process.py proves the
real wire against a live stub daemon.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from superlocalmemory.core.mcp_embedder_proxy import McpEmbedderProxy

_GATE = "superlocalmemory.core.outbound_http"


class TestDefaultsUnchanged:
    def test_default_timeout_and_non_strict(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        assert proxy._timeout == 5.0
        assert proxy._strict is False

    def test_non_strict_returns_nones_on_error(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        with patch(f"{_GATE}.post_json", side_effect=httpx.ConnectError("refused")):
            assert proxy.embed_batch(["a", "b"]) == [None, None]

    def test_negative_ping_not_cached(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        with patch(f"{_GATE}.urlopen", side_effect=OSError("refused")):
            assert proxy.is_available() is False
        ok = MagicMock()
        ok.status = 200
        ok.__enter__ = MagicMock(return_value=ok)
        ok.__exit__ = MagicMock(return_value=False)
        with patch(f"{_GATE}.urlopen", return_value=ok):
            assert proxy.is_available() is True  # retried, not sticky-False


class TestStrictMode:
    def test_strict_reraises_connect_error(self) -> None:
        proxy = McpEmbedderProxy(port=9999, strict=True)
        with patch(f"{_GATE}.post_json", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(httpx.ConnectError):
                proxy.embed_batch(["a"])

    def test_strict_reraises_read_timeout(self) -> None:
        proxy = McpEmbedderProxy(port=9999, strict=True)
        with patch(f"{_GATE}.post_json", side_effect=httpx.ReadTimeout("slow")):
            with pytest.raises(httpx.ReadTimeout):
                proxy.embed_batch(["a"])

    def test_strict_passes_through_success(self) -> None:
        proxy = McpEmbedderProxy(port=9999, timeout=30.0, strict=True)
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1, 0.2]]}
        resp.raise_for_status = MagicMock()
        with patch(f"{_GATE}.post_json", return_value=resp) as post:
            assert proxy.embed_batch(["x"]) == [[0.1, 0.2]]
        assert post.call_args.kwargs["timeout"] == 30.0
