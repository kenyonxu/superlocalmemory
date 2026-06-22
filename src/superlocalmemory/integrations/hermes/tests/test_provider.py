"""Tests for SuperLocalMemoryProvider — Chunk 1: skeleton + config parsing."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.integrations.hermes import SuperLocalMemoryProvider


class TestProviderSkeleton:
    """Chunk 1: 骨架与配置解析."""

    def test_is_available_when_import_fails(self):
        """当 superlocalmemory 不可 import 时返回 False."""
        provider = SuperLocalMemoryProvider()
        with patch.object(importlib, "import_module", side_effect=ImportError("no module")):
            assert provider.is_available() is False

    def test_is_available_when_import_succeeds(self):
        """当 superlocalmemory 可 import 时返回 True."""
        provider = SuperLocalMemoryProvider()
        assert provider.is_available() is True

    def test_name_property(self):
        """name 返回 'superlocalmemory'."""
        provider = SuperLocalMemoryProvider()
        assert provider.name == "superlocalmemory"

    def test_get_config_schema_returns_expected_keys(self):
        """schema 包含 mslm_profile, mode, include_global, include_shared."""
        provider = SuperLocalMemoryProvider()
        schema = provider.get_config_schema()
        assert isinstance(schema, list)
        keys = {item["key"] for item in schema}
        assert "mslm_profile" in keys
        assert "mode" in keys
        assert "include_global" in keys
        assert "include_shared" in keys

    @pytest.mark.parametrize("value,default,expected", [
        # None → default
        (None, True, True),
        (None, False, False),
        # bool passthrough
        (True, False, True),
        (False, True, False),
        # string "true"/"false"
        ("true", False, True),
        ("false", True, False),
        ("True", False, True),
        ("False", True, False),
        ("TRUE", False, True),
        ("FALSE", True, False),
        # string "1"/"0"
        ("1", False, True),
        ("0", True, False),
        # string "yes"/"no"
        ("yes", False, True),
        ("no", True, False),
        ("YES", False, True),
        ("NO", True, False),
        # string "on"/"off"
        ("on", False, True),
        ("off", True, False),
        ("ON", False, True),
        ("OFF", True, False),
        # int 1/0
        (1, False, True),
        (0, True, False),
    ])
    def test_parse_bool_with_various_inputs(self, value, default, expected):
        """_parse_bool 正确处理 None, bool, str, int 类型."""
        result = SuperLocalMemoryProvider._parse_bool(value, default)
        assert result is expected

    def test_load_hermes_config_returns_empty_when_config_raises(self):
        """load_config 抛出异常时返回空 dict."""
        provider = SuperLocalMemoryProvider()
        with patch("hermes_cli.config.load_config", side_effect=Exception("fail")):
            result = provider._load_hermes_config("/nonexistent")
        assert result == {}

    def test_load_hermes_config_returns_superlocalmemory_section(self):
        """返回 config.yaml 中 memory.superlocalmemory section."""
        provider = SuperLocalMemoryProvider()

        fake_mem_config = {
            "superlocalmemory": {
                "mslm_profile": "test-profile",
                "mode": "B",
            }
        }

        # We need to mock load_config to return our fake config
        with patch("hermes_cli.config.load_config") as mock_load:
            mock_load.return_value = {"memory": fake_mem_config}
            result = provider._load_hermes_config("/fake/home")
            assert result == {"mslm_profile": "test-profile", "mode": "B"}

    def test_initial_fields_are_none(self):
        """初始化后各字段为 None/False/""."""
        provider = SuperLocalMemoryProvider()
        assert provider._engine is None
        assert provider._session_id == ""
        assert provider._mslm_profile == ""
        assert provider._include_global is True
        assert provider._include_shared is False
        assert provider._cron_skipped is False
        assert provider._init_cancelled is False
        assert provider._prefetch_cache == ""


class TestInitialize:
    """Chunk 2: 初始化与引擎生命周期."""

    def test_initialize_loads_config_and_sets_profile(self, provider):
        """从 kwargs['agent_identity'] 映射 profile."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            mock_config = MockConfig.load.return_value
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")

        assert provider._mslm_profile == "coder"
        assert provider._session_id == "session_1"
        assert provider._slm_config is mock_config
        assert mock_config.active_profile == "coder"
        assert provider._engine is mock_engine

    def test_initialize_uses_config_override(self, provider):
        """Hermes config 中的 mslm_profile 覆盖 agent_identity."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()

            with patch.object(provider, "_load_hermes_config", return_value={
                "mslm_profile": "from-config",
            }):
                provider.initialize("session_1", agent_identity="coder")

        assert provider._mslm_profile == "from-config"

    def test_initialize_sets_mode_from_override(self, provider):
        """config.yaml 中的 mode 覆盖 MSLM 默认值."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine, \
             patch("superlocalmemory.storage.models.Mode") as MockMode:
            mock_config = MockConfig.load.return_value
            MockEngine.return_value = MagicMock()

            class _FakeModeVal:
                """Duck-typed Mode value with .name attribute."""
                def __init__(self, name):
                    self.name = name
            MockMode.__getitem__.side_effect = _FakeModeVal

            with patch.object(provider, "_load_hermes_config", return_value={
                "mode": "B",
            }):
                provider.initialize("session_1", agent_identity="coder")

        assert mock_config.mode.name == "B"

    def test_initialize_cron_context_skips(self, provider):
        """agent_context='cron' 时设置 _cron_skipped=True，不创建 engine."""
        provider.initialize("session_1", agent_context="cron", agent_identity="coder")
        assert provider._cron_skipped is True
        assert provider._engine is None

    def test_initialize_timeout_cleans_up(self, provider):
        """engine.initialize() 超时后设置 _init_cancelled，释放 _engine."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine, \
             patch("superlocalmemory.integrations.hermes._INIT_TIMEOUT", 0.01), \
             patch("threading.Thread") as MockThread:

            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()

            mock_thread = MagicMock()
            mock_thread.is_alive.side_effect = [True, True, False]
            MockThread.return_value = mock_thread

            provider.initialize("session_1", agent_identity="coder")

        assert provider._init_cancelled is True
        assert provider._engine is None

    def test_initialize_exception_disables_provider(self, provider):
        """engine.initialize() 抛异常后 _engine = None."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()

            with patch("threading.Thread", wraps=__import__("threading").Thread):
                mock_engine = MockEngine.return_value
                mock_engine.initialize.side_effect = RuntimeError("init failure")

                provider.initialize("session_1", agent_identity="coder")

        assert provider._engine is None

    def test_initialize_creates_speaker_entities(self, provider):
        """成功初始化后调用 create_speaker_entities('user', 'hermes')."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")

        mock_engine.create_speaker_entities.assert_called_once_with("user", "hermes")

    def test_initialize_speaker_entities_non_fatal(self, provider):
        """create_speaker_entities 失败不中断初始化."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.create_speaker_entities.side_effect = ValueError("bad entities")

            provider.initialize("session_1", agent_identity="coder")

        assert provider._engine is mock_engine
        assert provider._session_id == "session_1"

    def test_ensure_engine_returns_true_when_ready(self, provider):
        """_ensure_engine 在 engine 就绪时返回 True."""
        provider._engine = MagicMock()
        assert provider._ensure_engine() is True

    def test_ensure_engine_returns_false_when_none(self, provider):
        """_ensure_engine 在 engine 为 None 时返回 False."""
        provider._engine = None
        assert provider._ensure_engine() is False

    def test_shutdown_clears_engine(self, provider):
        """shutdown 清除 _engine 引用."""
        import threading
        provider._engine = MagicMock()
        provider._sync_thread = threading.Thread(target=lambda: None)
        provider._sync_thread.start()

        provider.shutdown()

        assert provider._engine is None

    def test_parse_bool_applied_to_include_global(self, provider):
        """include_global 通过 _parse_bool 解析."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine, \
             patch.object(provider, "_load_hermes_config", return_value={
                 "include_global": "false",
             }):
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()

            provider.initialize("session_1", agent_identity="coder")

        assert provider._include_global is False

    def test_parse_bool_applied_to_include_shared(self, provider):
        """include_shared 通过 _parse_bool 解析."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine, \
             patch.object(provider, "_load_hermes_config", return_value={
                 "include_shared": "true",
             }):
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()

            provider.initialize("session_1", agent_identity="coder")

        assert provider._include_shared is True


class TestPrefetch:
    """Chunk 3: prefetch 混合模式."""

    def _make_recall_response(self, items: list[dict]) -> MagicMock:
        """Build a mock RecallResponse with retrieval results."""
        from superlocalmemory.storage.models import AtomicFact

        response = MagicMock()
        response.query_type = "factual"
        response.retrieval_time_ms = 100.0
        response.no_confident_match = False
        results = []
        for item in items:
            fact = MagicMock(spec=AtomicFact)
            fact.fact_id = item.get("fact_id", "f_" + str(len(results)))
            fact.content = item["content"]
            fact.confidence = item.get("confidence", 0.5)
            fact.signals = []
            result = MagicMock()
            result.fact = fact
            result.score = item.get("score", 0.5)
            result.confidence = item.get("confidence", 0.5)
            result.channel_scores = item.get("channel_scores", {})
            results.append(result)
        response.results = results
        return response

    def test_prefetch_first_turn_sync_recall(self, provider):
        """Turn 1: 无缓存，同步调用 engine.recall()."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.recall.return_value = self._make_recall_response([
                {"content": "user likes dark mode", "score": 0.92, "confidence": 0.87},
            ])

            provider.initialize("session_1", agent_identity="coder")
            result = provider.prefetch("what theme?")

        assert "dark mode" in result
        mock_engine.recall.assert_called_once()

    def test_prefetch_subsequent_turn_uses_cache(self, provider):
        """Turn 2: 消费 _prefetch_cache，不调用 engine.recall()."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")

            # Simulate a filled prefetch cache (as would be done by queue_prefetch)
            with provider._prefetch_lock:
                provider._prefetch_cache = "cached result for theme"
                provider._prefetch_fired_at = 1

            result = provider.prefetch("what theme?")
            assert result == "cached result for theme"
            # Ensure we did NOT call recall again
            mock_engine.recall.assert_not_called()

    def test_prefetch_empty_query_returns_empty(self, provider):
        """query 为空时直接返回 ''."""
        result = provider.prefetch("")
        assert result == ""

    def test_prefetch_engine_none_returns_empty(self, provider):
        """engine 未初始化时返回 ''."""
        assert provider._engine is None
        result = provider.prefetch("test query")
        assert result == ""

    def test_prefetch_cron_skipped_returns_empty(self, provider):
        """_cron_skipped=True 时返回 ''."""
        provider._cron_skipped = True
        result = provider.prefetch("test query")
        assert result == ""

    def test_queue_prefetch_starts_background_thread(self, provider):
        """queue_prefetch 启动 daemon thread 调用 engine.recall()."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.recall.return_value = self._make_recall_response([
                {"content": "prefetched memory", "score": 0.8},
            ])

            provider.initialize("session_1", agent_identity="coder")
            provider.queue_prefetch("next query")

        # The daemon thread should have called recall
        import time
        time.sleep(0.1)  # Give daemon thread time to run
        mock_engine.recall.assert_called()

    def test_queue_prefetch_writes_cache(self, provider):
        """后台线程完成后 _prefetch_cache 被写入."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.recall.return_value = self._make_recall_response([
                {"content": "cached memory data", "score": 0.85},
            ])

            provider.initialize("session_1", agent_identity="coder")
            provider.queue_prefetch("next query")

        import time
        time.sleep(0.2)  # Wait for daemon thread
        assert provider._prefetch_cache != ""
        assert "cached memory" in provider._prefetch_cache

    def test_queue_prefetch_concurrent_safety(self, provider):
        """连续调用 queue_prefetch 不会启动重叠线程."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.recall.return_value = self._make_recall_response([
                {"content": "data", "score": 0.8},
            ])

            provider.initialize("session_1", agent_identity="coder")
            provider.queue_prefetch("query 1")
            thread_1 = provider._prefetch_thread
            provider.queue_prefetch("query 2")

        # Second call should not create a new thread if first is still running
        if thread_1 and thread_1.is_alive():
            # If first thread is still running, second should be the same thread
            assert provider._prefetch_thread is thread_1
        else:
            # If first completed, second should be a new thread
            assert provider._prefetch_thread is not None

    def test_prefetch_lock_protects_cache(self, provider):
        """_prefetch_lock 保护缓存读写."""
        assert hasattr(provider, "_prefetch_lock")
        assert provider._prefetch_lock is not None
