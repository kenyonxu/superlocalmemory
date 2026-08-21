"""EmbeddingService daemon fallback: singleton held / memory pressure."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.embeddings import EmbeddingService


def _svc() -> EmbeddingService:
    return EmbeddingService(EmbeddingConfig(dimension=384))


class TestAttach:
    def test_attach_on_singleton_held_and_daemon_online(self) -> None:
        svc = _svc()
        proxy = MagicMock()
        proxy.is_available.return_value = True
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=False), \
             patch("superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy", return_value=proxy):
            svc._ensure_worker()
        assert svc._available is False           # 内部事实不变
        assert svc._daemon_fallback is proxy

    def test_no_attach_when_daemon_offline(self) -> None:
        svc = _svc()
        proxy = MagicMock()
        proxy.is_available.return_value = False
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=False), \
             patch("superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy", return_value=proxy):
            svc._ensure_worker()
        assert svc._available is False
        assert svc._daemon_fallback is None

    def test_attach_on_memory_pressure(self) -> None:
        svc = _svc()
        proxy = MagicMock()
        proxy.is_available.return_value = True
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=True), \
             patch("superlocalmemory.core.embeddings._is_embedding_worker_alive", return_value=False), \
             patch.object(EmbeddingService, "_check_memory_pressure", return_value=False), \
             patch("superlocalmemory.core.embeddings.release_embedding_lock"), \
             patch("superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy", return_value=proxy):
            svc._ensure_worker()
        assert svc._daemon_fallback is proxy

    def test_env_opt_out(self, monkeypatch) -> None:
        monkeypatch.setenv("SLM_EMBED_DAEMON_FALLBACK", "0")
        svc = _svc()
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=False):
            svc._ensure_worker()
        assert svc._available is False
        assert svc._daemon_fallback is None


class TestDelegation:
    def test_embed_delegates_to_proxy_when_disabled(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.return_value = [[0.1] * 384]
        svc._daemon_fallback = proxy
        assert svc.embed("hello") == [0.1] * 384
        assert svc._fallback_served == 1

    def test_none_when_no_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        assert svc.embed("hello") is None  # 现状不劣化

    def test_none_availability_never_short_circuits_to_proxy(self) -> None:
        # 三态防线:None 是 heal 重探测信号,必须 fall through 到 _ensure_worker
        svc = _svc()
        svc._available = None
        svc._daemon_fallback = MagicMock()
        # Seam adjustment (controller ruling 1): EmbeddingService has no
        # _send_request method; the worker reply is intercepted at
        # _readline_with_timeout, which is the real I/O seam the brief's
        # fictional _send_request stood in for.
        with patch.object(EmbeddingService, "_ensure_worker") as ensure, \
             patch.object(
                 EmbeddingService, "_readline_with_timeout",
                 return_value=json.dumps({"ok": True, "vectors": [[0.1] * 384]}),
             ):
            svc._worker_proc = MagicMock()
            svc._worker_proc.poll.return_value = None
            svc.embed("hello")
        ensure.assert_called_once()
        svc._daemon_fallback.embed_batch.assert_not_called()

    def test_first_call_after_attach_delegates_immediately(self) -> None:
        # Fix round 1: the call that loses the singleton race and attaches the
        # fallback must itself be served by the daemon — no first-call None.
        svc = _svc()
        proxy = MagicMock()
        proxy.is_available.return_value = True
        proxy.embed_batch.return_value = [[0.1] * 384]
        with patch(
            "superlocalmemory.core.embeddings.acquire_embedding_lock",
            return_value=False,
        ), patch(
            "superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy",
            return_value=proxy,
        ), patch.object(
            EmbeddingService, "_ensure_worker", wraps=svc._ensure_worker,
        ) as ensure_spy:
            # First call: attaches the fallback AND returns the vector.
            assert svc.embed("x") == [0.1] * 384
            # Second call: top short-circuit serves it — _ensure_worker must
            # NOT run again (no double-delegation through the attach path).
            assert svc.embed("x") == [0.1] * 384
        assert ensure_spy.call_count == 1
        assert proxy.embed_batch.call_count == 2


class TestFailureCounting:
    def test_detach_after_three_connect_errors(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.side_effect = httpx.ConnectError("refused")
        svc._daemon_fallback = proxy
        for _ in range(3):
            assert svc.embed("x") is None
        assert svc._daemon_fallback is None  # detached → 回到现状

    def test_read_timeout_counts_only_after_two_consecutive(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        svc._daemon_fallback = proxy
        proxy.embed_batch.side_effect = httpx.ReadTimeout("slow")
        svc.embed("x")
        assert svc._fallback_fail_count == 0   # 首次宽容
        svc.embed("x")
        assert svc._fallback_fail_count == 1   # 连续第二次计 1
        # Mock-semantic fix (brief wrote side_effect=[[0.1]*384], which makes
        # MagicMock RETURN the flat vector [0.1]*384 as the batch and crashes
        # embed()'s dimension check). One extra nesting level returns the
        # intended one-item batch [[0.1]*384]; the reset assertion is unchanged.
        proxy.embed_batch.side_effect = [[[0.1] * 384]]  # 成功重置
        svc.embed("x")
        assert svc._fallback_read_timeouts == 0


class TestEdgeInputRobustness:
    """Fix round 1: env parse crashes, None-padded daemon results,
    consecutive-read-timeout chain semantics, counter resets."""

    def test_malformed_port_env_does_not_crash(self, monkeypatch) -> None:
        monkeypatch.setenv("SLM_DAEMON_PORT", "abc")
        svc = _svc()
        with patch("superlocalmemory.core.embeddings.acquire_embedding_lock", return_value=False):
            svc._ensure_worker()  # must not raise ValueError
        assert svc._daemon_fallback is None

    def test_none_padded_result_treated_as_failure(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.return_value = [[0.1] * 384, None]
        svc._daemon_fallback = proxy
        result = svc.embed_batch(["a", "b"])
        # Graceful: embed_batch never returns None itself; the internal
        # _subprocess_embed result is None, surfaced as the pre-fallback
        # None-list behavior instead of crashing in _validate_dimension.
        assert result == [None, None]
        assert svc._fallback_fail_count == 1

    def test_read_timeout_chain_broken_by_other_error(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        svc._daemon_fallback = proxy
        proxy.embed_batch.side_effect = [
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("slow"),
        ]
        svc.embed("x")   # rt=1, tolerated
        assert svc._fallback_fail_count == 0
        svc.embed("x")   # connect error: counts 1 AND breaks the rt chain
        svc.embed("x")   # rt=1 again (chain restarted) — still tolerated
        assert svc._fallback_fail_count == 1
        assert svc._fallback_read_timeouts == 1

    def test_counters_reset_on_attach(self) -> None:
        svc = _svc()
        svc._fallback_read_timeouts = 1
        svc._fallback_fail_count = 2
        proxy = MagicMock()
        proxy.is_available.return_value = True
        with patch("superlocalmemory.core.mcp_embedder_proxy.McpEmbedderProxy", return_value=proxy):
            svc._try_attach_daemon_fallback()
        assert svc._daemon_fallback is proxy
        assert svc._fallback_fail_count == 0
        assert svc._fallback_read_timeouts == 0


class TestExternalSemantics:
    def test_is_available_true_with_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        svc._daemon_fallback = MagicMock()
        assert svc.is_available is True

    def test_is_available_false_without_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        assert svc.is_available is False

    def test_is_warm_after_fallback_served(self) -> None:
        svc = _svc()
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.return_value = [[0.1] * 384]
        svc._daemon_fallback = proxy
        assert svc.is_warm is False
        svc.embed("x")
        assert svc.is_warm is True

    def test_embedder_mode(self) -> None:
        svc = _svc()
        assert svc.embedder_mode == "local"
        svc._available = False
        assert svc.embedder_mode == "unavailable"
        svc._daemon_fallback = MagicMock()
        assert svc.embedder_mode == "daemon-fallback"

    def test_dimension_mismatch_message_names_daemon_fallback(self) -> None:
        from superlocalmemory.core.embeddings import DimensionMismatchError
        svc = _svc()  # dimension=384
        svc._available = False
        proxy = MagicMock()
        proxy.embed_batch.return_value = [[0.1] * 8]  # 错维度
        svc._daemon_fallback = proxy
        with pytest.raises(DimensionMismatchError, match="daemon fallback"):
            svc.embed("x")

    def test_unload_is_noop_for_fallback(self) -> None:
        svc = _svc()
        svc._available = False
        svc._daemon_fallback = MagicMock()
        assert svc.unload() in (True, False)  # 不抛异常即通过
        assert svc._daemon_fallback is not None  # fallback 存活,不受影响
