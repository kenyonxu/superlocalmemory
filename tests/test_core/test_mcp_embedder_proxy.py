# tests/test_core/test_mcp_embedder_proxy.py
"""Tests for McpEmbedderProxy strict mode and timeout configurability."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from superlocalmemory.core.mcp_embedder_proxy import McpEmbedderProxy


class TestDefaultsUnchanged:
    def test_default_timeout_and_non_strict(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        assert proxy._timeout == 5.0
        assert proxy._strict is False

    def test_non_strict_returns_nones_on_error(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            assert proxy.embed_batch(["a", "b"]) == [None, None]

    def test_negative_ping_not_cached(self) -> None:
        proxy = McpEmbedderProxy(port=9999)
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            assert proxy.is_available() is False
        ok = MagicMock(); ok.status_code = 200
        with patch("httpx.get", return_value=ok):
            assert proxy.is_available() is True  # retried, not sticky-False


class TestStrictMode:
    def test_strict_reraises_connect_error(self) -> None:
        proxy = McpEmbedderProxy(port=9999, strict=True)
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(httpx.ConnectError):
                proxy.embed_batch(["a"])

    def test_strict_reraises_read_timeout(self) -> None:
        proxy = McpEmbedderProxy(port=9999, strict=True)
        with patch("httpx.post", side_effect=httpx.ReadTimeout("slow")):
            with pytest.raises(httpx.ReadTimeout):
                proxy.embed_batch(["a"])

    def test_strict_passes_through_success(self) -> None:
        proxy = McpEmbedderProxy(port=9999, timeout=30.0, strict=True)
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1, 0.2]]}
        resp.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=resp) as post:
            assert proxy.embed_batch(["x"]) == [[0.1, 0.2]]
        assert post.call_args.kwargs["timeout"] == 30.0
