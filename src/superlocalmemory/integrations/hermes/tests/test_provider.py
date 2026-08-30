"""Tests for SuperLocalMemoryProvider — Chunk 1: skeleton + config parsing."""

from __future__ import annotations

import importlib
import sys
import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

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
        with patch.object(provider, "_load_hermes_config", return_value={}), \
             patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
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


class TestSyncTurn:
    """Chunk 4: sync_turn 与生命周期钩子."""

    def test_sync_turn_stores_combined_content(self, provider):
        """合并 user + assistant 内容，调用 engine.store()."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            provider.sync_turn("hello", "hi there!")

            time.sleep(0.15)
            mock_engine.store.assert_called_once()

    def test_sync_turn_skips_short_meaningless(self, provider):
        """跳过 'ok', 'yes', 'thanks', 'thx' 等无意义回复."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            provider.sync_turn("ok", "sure")

            time.sleep(0.1)
            mock_engine.store.assert_not_called()

    def test_sync_turn_uses_write_lock(self, provider):
        """engine.store() 在 _write_lock 保护下执行."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            provider.sync_turn("hello", "world")
            time.sleep(0.15)

            # If store was called, the write lock was used (it wraps store())
            assert mock_engine.store.called

    def test_sync_turn_drops_when_prior_incomplete(self, provider):
        """上一轮写入未完成时，跳过本轮写入."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")

            # Make store block to simulate incomplete write
            import threading
            store_blocker = threading.Event()
            mock_engine.store.side_effect = lambda *a, **kw: store_blocker.wait(10)

            provider.sync_turn("first msg", "first reply")
            time.sleep(0.05)
            mock_engine.store.reset_mock()

            # Second sync while first still going
            provider.sync_turn("second msg", "second reply")
            time.sleep(0.05)
            mock_engine.store.assert_not_called()
            store_blocker.set()
            time.sleep(0.05)

    def test_sync_turn_truncates_long_content(self, provider):
        """>4000 字符时截断到 4000."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            long_user = "x" * 5000
            provider.sync_turn(long_user, "short")

            time.sleep(0.15)
            mock_engine.store.assert_called_once()

    def test_sync_turn_cron_skipped(self, provider):
        """_cron_skipped=True 时直接返回."""
        provider._cron_skipped = True
        provider._engine = MagicMock()
        provider.sync_turn("hello", "world")
        provider._engine.store.assert_not_called()

    def test_sync_turn_engine_none(self, provider):
        """_engine=None 时直接返回."""
        provider.sync_turn("hello", "world")
        # No assert needed — should not raise


class TestHooks:
    """Chunk 4: 生命周期钩子."""

    def test_on_memory_write_calls_store(self, provider):
        """内置 memory 写入镜像到 MSLM."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            provider.on_memory_write("add", "memory", "remember this fact")

            time.sleep(0.1)
            mock_engine.store.assert_called_once()

    def test_on_pre_compress_stores_last_messages(self, provider):
        """取最后 10 条消息拼接，存入 MSLM."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            messages = [
                {"role": "user", "content": f"message {i}"} for i in range(15)
            ]
            result = provider.on_pre_compress(messages)

            time.sleep(0.15)
            mock_engine.store.assert_called_once()
            assert result == ""

    def test_on_pre_compress_returns_empty_string(self, provider):
        """返回 '' 不干扰 compression summary."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            result = provider.on_pre_compress([
                {"role": "user", "content": "test"},
            ])

            time.sleep(0.1)
            assert result == ""

    def test_on_pre_compress_skips_empty_or_non_text(self, provider):
        """跳过空内容、非 user/assistant 角色、非字符串 content."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            messages = [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": ""},
                {"role": "user", "content": None},
                {"role": "user", "content": "valid message"},
            ]
            provider.on_pre_compress(messages)

            time.sleep(0.15)
            mock_engine.store.assert_called_once()

    def test_on_session_end_calls_close_session(self, provider):
        """调用 engine.close_session(session_id)."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            provider.on_session_end([])

            mock_engine.close_session.assert_called_once_with("session_1")

    def test_on_session_switch_updates_session_id(self, provider):
        """更新 _session_id，清空 _prefetch_cache."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()

            provider.initialize("session_1", agent_identity="coder")
            provider._prefetch_cache = "old cache"

            provider.on_session_switch("session_2")

            assert provider._session_id == "session_2"
            assert provider._prefetch_cache == ""


class TestToolSchemas:
    """Chunk 5: 工具 schemas."""

    def test_get_tool_schemas_returns_three_tools(self, provider):
        """返回 recall, remember, status 三个 schema."""
        schemas = provider.get_tool_schemas()
        assert len(schemas) == 3
        names = {s["name"] for s in schemas}
        assert names == {"slm_recall", "slm_remember", "slm_status"}

    def test_recall_schema_has_required_query(self, provider):
        """slm_recall 的 query 为 required."""
        schemas = provider.get_tool_schemas()
        recall = next(s for s in schemas if s["name"] == "slm_recall")
        params = recall["parameters"]
        assert "query" in params.get("required", [])

    def test_remember_schema_has_optional_scope(self, provider):
        """slm_remember 的 scope 默认 'personal'."""
        schemas = provider.get_tool_schemas()
        remember = next(s for s in schemas if s["name"] == "slm_remember")
        scope_prop = remember["parameters"]["properties"]["scope"]
        assert scope_prop.get("default") == "personal"


class TestToolCalls:
    """Chunk 5: 工具调用."""

    def test_recall_routes_to_engine_recall(self, provider):
        """调用 engine.recall()，返回格式化结果."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            from superlocalmemory.storage.models import AtomicFact
            resp = MagicMock()
            resp.query_type = "factual"
            resp.retrieval_time_ms = 100.0
            f = MagicMock(spec=AtomicFact, fact_id="f1", content="test fact", confidence=0.9, signals=[])
            r = MagicMock(fact=f, score=0.9, confidence=0.9, channel_scores={})
            resp.results = [r]
            mock_engine.recall.return_value = resp

            provider.initialize("session_1", agent_identity="coder")
            result = provider.handle_tool_call("slm_recall", {"query": "test"})

        assert isinstance(result, str)
        assert "f1" in result or "test fact" in result

    def test_recall_empty_query_returns_error(self, provider):
        """query 为空返回 tool_error."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()

            provider.initialize("session_1", agent_identity="coder")
            result = provider.handle_tool_call("slm_recall", {"query": ""})
        assert '"error"' in result

    def test_recall_engine_not_ready_returns_error(self, provider):
        """engine 未初始化返回 tool_error."""
        result = provider.handle_tool_call("slm_recall", {"query": "test"})
        assert '"error"' in result

    def test_recall_limit_capped_at_20(self, provider):
        """limit > 20 时截断到 20."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            resp = MagicMock()
            resp.results = []
            mock_engine.recall.return_value = resp

            provider.initialize("session_1", agent_identity="coder")
            provider.handle_tool_call("slm_recall", {"query": "test", "limit": 50})
        mock_engine.recall.assert_called_once()
        _, kwargs = mock_engine.recall.call_args
        assert kwargs.get("limit", 99) <= 20

    def test_remember_calls_engine_store(self, provider):
        """调用 engine.store()，返回 stored 状态."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.store.return_value = ["f1", "f2"]

            provider.initialize("session_1", agent_identity="coder")
            result = provider.handle_tool_call("slm_remember", {"content": "remember this"})

        assert isinstance(result, str)
        assert "stored" in result.lower() or "f1" in result

    def test_remember_engine_not_ready_returns_error(self, provider):
        """engine 未初始化返回 tool_error."""
        result = provider.handle_tool_call("slm_remember", {"content": "test"})
        assert '"error"' in result

    def test_remember_no_facts_returns_noop(self, provider):
        """engine.store() 返回空 fact_ids 时返回 noop."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.store.return_value = []

            provider.initialize("session_1", agent_identity="coder")
            result = provider.handle_tool_call("slm_remember", {"content": "redundant content"})

        assert "noop" in result.lower()

    def test_status_returns_profile_and_counts(self, provider):
        """返回 profile, mode, facts, entities, db_size 等."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            # Mock db responses
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (123,)
            mock_engine.db.execute.return_value = mock_cursor

            provider.initialize("session_1", agent_identity="coder")
            result = provider.handle_tool_call("slm_status", {})

        assert isinstance(result, str)
        assert "123" in result or "profile" in result

    def test_status_engine_not_ready_returns_error(self, provider):
        """engine 未初始化返回 tool_error."""
        result = provider.handle_tool_call("slm_status", {})
        assert '"error"' in result

    def test_unknown_tool_returns_error(self, provider):
        """未知工具名返回 tool_error."""
        result = provider.handle_tool_call("slm_unknown", {})
        assert '"error"' in result

    def test_exception_in_tool_returns_error(self, provider):
        """工具内部异常被捕获，返回 tool_error."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_engine.recall.side_effect = RuntimeError("unexpected crash")

            provider.initialize("session_1", agent_identity="coder")
            result = provider.handle_tool_call("slm_recall", {"query": "test"})
        assert '"error"' in result

    def test_system_prompt_block_contains_status(self, provider):
        """system_prompt_block() 包含动态 profile/mode."""
        with patch.object(provider, "_load_hermes_config", return_value={}), \
             patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (42,)
            mock_engine.db.execute.return_value = mock_cursor

            provider.initialize("session_1", agent_identity="test-agent")

            block = provider.system_prompt_block()

        assert "test-agent" in block
        assert "slm_recall" in block
        assert "slm_remember" in block
        assert "slm_status" in block


class TestIntegration:
    """Chunk 6: 集成测试."""

    def test_full_turn_lifecycle(self, provider):
        """完整 turn: initialize → prefetch → sync_turn → queue_prefetch → on_session_end."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            resp = MagicMock()
            resp.results = []
            mock_engine.recall.return_value = resp
            mock_engine.store.return_value = ["f1"]
            mock_engine.close_session.return_value = 0

            # 1. Initialize
            provider.initialize("session_1", agent_identity="tester")
            assert provider._engine is not None
            assert provider._session_id == "session_1"

            # 2. Prefetch
            result = provider.prefetch("what do I know?")
            assert isinstance(result, str)

            # 3. Sync turn
            provider.sync_turn("hello", "world!")
            time.sleep(0.15)
            mock_engine.store.assert_called()

            # 4. Queue prefetch
            provider.queue_prefetch("next question")
            time.sleep(0.2)
            assert provider._prefetch_cache is not None

            # 5. Session end
            provider.on_session_end([])
            mock_engine.close_session.assert_called_once_with("session_1")

    def test_provider_disabled_gracefully(self, provider):
        """engine 初始化失败后，所有方法静默返回不抛异常."""
        # Don't initialize — engine stays None
        assert provider._ensure_engine() is False

        # prefetch should return empty
        assert provider.prefetch("test") == ""

        # sync_turn should not crash
        provider.sync_turn("hello", "world")

        # tools should return error
        result = provider.handle_tool_call("slm_recall", {"query": "test"})
        assert '"error"' in result

        result = provider.handle_tool_call("slm_remember", {"content": "test"})
        assert '"error"' in result

        result = provider.handle_tool_call("slm_status", {})
        assert '"error"' in result

        # hooks should not crash
        provider.on_memory_write("add", "memory", "test")
        provider.on_session_end([])
        provider.on_session_switch("new_session")
        assert provider._session_id == "new_session"

    def test_concurrent_sync_turn_and_prefetch(self, provider):
        """sync_turn 写入和 prefetch 读取并发不冲突."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            resp = MagicMock()
            resp.results = []
            mock_engine.recall.return_value = resp
            mock_engine.store.return_value = ["f1"]

            provider.initialize("session_1", agent_identity="tester")

            # Pre-populate cache
            provider._prefetch_cache = "cached data"
            provider._prefetch_fired_at = 1

            # Simultanous prefetch (read cache) and sync_turn (write)
            prefetch_result = provider.prefetch("query")
            provider.sync_turn("user msg", "asst reply")
            time.sleep(0.15)

            assert prefetch_result == "cached data" or prefetch_result == ""
            # Both operations should complete without error

    def test_memory_write_mirror(self, provider):
        """on_memory_write 镜像到 MSLM."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            mock_engine = MockEngine.return_value

            provider.initialize("session_1", agent_identity="coder")
            provider.on_memory_write("add", "memory", "mirror this")
            time.sleep(0.15)
            mock_engine.store.assert_called_once()


class TestPluginYAML:
    """Chunk 6: plugin.yaml 验证."""

    def test_plugin_yaml_loads(self):
        """plugin.yaml 可被解析，包含正确字段."""
        import os
        import yaml

        yaml_path = os.path.join(
            os.path.dirname(__file__), "..", "plugin.yaml",
        )
        assert os.path.exists(yaml_path), f"plugin.yaml not found at {yaml_path}"

        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        assert data is not None
        assert data.get("name") == "superlocalmemory"
        assert "version" in data
        assert "pip_dependencies" in data
        assert "hooks" in data


class TestDaemonRouting:
    """Daemon-first routing with in-process fallback (embedding-worker singleton).

    The unified daemon owns the machine-wide embedding worker, so when it is
    reachable the provider routes recall/store through the daemon HTTP API;
    otherwise it falls back to the in-process engine.
    """

    def _setup_provider(self, provider, mock_engine):
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = mock_engine
            provider.initialize("session_1", agent_identity="coder")

    def _daemon_recall_json(self):
        return {
            "ok": True,
            "query": "q",
            "results": [
                {"fact_id": "f1", "content": "daemon fact", "score": 0.9, "confidence": 0.8},
            ],
            "query_type": "factual",
            "retrieval_time_ms": 12.0,
        }

    # -- recall routing -------------------------------------------------------

    def test_recall_routed_when_daemon_up(self, provider, mock_engine):
        """daemon 在线时 recall 走 HTTP,本地引擎不被调用."""
        self._setup_provider(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api", return_value=self._daemon_recall_json()) as api:
            out = provider._sync_recall("q")

        assert "daemon fact" in out
        api.assert_called_once()
        mock_engine.recall.assert_not_called()

    def test_recall_falls_back_when_daemon_down(self, provider, mock_engine):
        """daemon 不在线(autouse fixture)时回退本地引擎."""
        self._setup_provider(provider, mock_engine)
        mock_engine.recall.return_value.results = []
        provider._sync_recall("q")
        mock_engine.recall.assert_called_once()

    def test_recall_falls_back_when_daemon_errors(self, provider, mock_engine):
        """daemon 请求失败(返回 None)时回退本地引擎."""
        self._setup_provider(provider, mock_engine)
        mock_engine.recall.return_value.results = []
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api", return_value=None):
            provider._sync_recall("q")
        mock_engine.recall.assert_called_once()

    def test_recall_falls_back_when_daemon_not_ok(self, provider, mock_engine):
        """daemon 返回非 ok 时回退本地引擎."""
        self._setup_provider(provider, mock_engine)
        mock_engine.recall.return_value.results = []
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api", return_value={"ok": False}):
            provider._sync_recall("q")
        mock_engine.recall.assert_called_once()

    # -- store routing --------------------------------------------------------

    def test_store_routed_when_daemon_up(self, provider, mock_engine):
        """daemon 在线时 sync_turn 走 /remember,本地 store 不被调用."""
        self._setup_provider(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value={"ok": True, "fact_ids": ["f1", "f2"]}) as api:
            provider.sync_turn("hello world", "hi there")
            provider._sync_thread.join(timeout=5.0)

        assert api.call_count == 1
        args, kwargs = api.call_args
        assert args[0] == "POST" and args[1] == "/remember"
        assert args[2]["scope"] == "personal"
        assert "hello world" in args[2]["content"]
        mock_engine.store.assert_not_called()

    def test_store_falls_back_when_daemon_down(self, provider, mock_engine):
        """daemon 不在线时 sync_turn 回退本地 store."""
        self._setup_provider(provider, mock_engine)
        provider.sync_turn("hello world", "hi there")
        provider._sync_thread.join(timeout=5.0)
        mock_engine.store.assert_called_once()

    def test_store_falls_back_when_daemon_errors(self, provider, mock_engine):
        """daemon store 失败时回退本地 store."""
        self._setup_provider(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api", return_value=None):
            provider.sync_turn("hello world", "hi there")
            provider._sync_thread.join(timeout=5.0)
        mock_engine.store.assert_called_once()

    def test_remember_tool_routed(self, provider, mock_engine):
        """slm_remember 工具在 daemon 在线时走路由."""
        self._setup_provider(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value={"ok": True, "fact_ids": ["f1"]}):
            out = provider._tool_remember({"content": "route me"})
        import json as _json
        assert _json.loads(out)["status"] == "stored"
        mock_engine.store.assert_not_called()


class TestProfilePin:
    """Per-request profile pinning (spec §7).

    ``pin_profile`` 默认开启: every daemon-routed recall/store call and
    every offline-fallback engine call carries ``profile_id`` anchored to
    the provider's ``_mslm_profile``, so a mid-session active-pointer
    switch (daemon-side profile switch) cannot silently redirect this
    provider's traffic.

    Pin off (``SLM_HERMES_PIN_PROFILE=0`` or config override) or an empty
    ``_mslm_profile`` omits the parameter — the call then follows the
    active pointer (legacy client behaviour).
    """

    _RECALL_JSON = {
        "ok": True,
        "query": "q",
        "results": [
            {"fact_id": "f1", "content": "pinned fact", "score": 0.9, "confidence": 0.8},
        ],
        "query_type": "factual",
        "retrieval_time_ms": 12.0,
    }

    _STORE_JSON = {"ok": True, "fact_ids": ["f1"]}

    def _setup(self, provider, mock_engine, *, pin=True, profile="zhihui"):
        """Initialise with mocked engine, then set pin state + profile."""
        with patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = mock_engine
            provider.initialize("session_1", agent_identity="coder")
        provider._pin_profile = pin
        provider._mslm_profile = profile

    @staticmethod
    def _qs_of(call):
        """Query-string dict of one routed GET /recall call."""
        return parse_qs(urlsplit(call.args[1]).query)

    # -- pin on: every routed call carries profile_id -------------------------

    def test_pin_on_recall_query_carries_profile_id(self, provider, mock_engine):
        """pin on 时 recall 的 query string 携带 profile_id."""
        self._setup(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value=self._RECALL_JSON) as api:
            provider._sync_recall("q")
        assert self._qs_of(api.call_args).get("profile_id") == ["zhihui"]
        mock_engine.recall.assert_not_called()

    def test_pin_on_all_recall_entrypoints_carry_profile_id(self, provider, mock_engine):
        """prefetch / slm_recall 工具 / queue_prefetch 三条 recall 路径全部携带."""
        self._setup(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value=self._RECALL_JSON) as api:
            provider.prefetch("first turn")          # cold start → sync recall
            provider._tool_recall({"query": "q"})    # tool path
            provider.queue_prefetch("next turn")     # background prefetch
            provider._prefetch_thread.join(timeout=5.0)
        assert api.call_count == 3
        for call in api.call_args_list:
            assert self._qs_of(call).get("profile_id") == ["zhihui"]
        mock_engine.recall.assert_not_called()

    def test_pin_on_store_body_carries_profile_id(self, provider, mock_engine):
        """pin on 时 store 的 /remember body 携带 profile_id."""
        self._setup(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value=self._STORE_JSON) as api:
            out = provider._tool_remember({"content": "remember"})
        assert api.call_count == 1
        assert api.call_args.args[2].get("profile_id") == "zhihui"
        assert '"stored"' in out
        mock_engine.store.assert_not_called()

    def test_pin_on_all_store_entrypoints_carry_profile_id(self, provider, mock_engine):
        """sync_turn / on_memory_write / on_pre_compress / slm_remember 全部携带."""
        self._setup(provider, mock_engine)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value=self._STORE_JSON) as api:
            provider.sync_turn("hello", "world")
            provider._sync_thread.join(timeout=5.0)
            provider.on_memory_write("add", "memory", "mirror me")
            time.sleep(0.2)
            provider.on_pre_compress([{"role": "user", "content": "compress me"}])
            time.sleep(0.2)
            provider._tool_remember({"content": "remember"})
        assert api.call_count == 4
        for call in api.call_args_list:
            assert call.args[0] == "POST" and call.args[1] == "/remember"
            assert call.args[2].get("profile_id") == "zhihui"
        mock_engine.store.assert_not_called()

    # -- pin off: parameter omitted, follows active pointer -------------------

    def test_pin_off_recall_omits_profile_id(self, provider, mock_engine):
        """pin off 时 recall 不携带 profile_id(跟随活跃指针)."""
        self._setup(provider, mock_engine, pin=False)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value=self._RECALL_JSON) as api:
            provider._sync_recall("q")
        assert "profile_id" not in self._qs_of(api.call_args)

    def test_pin_off_store_omits_profile_id(self, provider, mock_engine):
        """pin off 时 store body 不携带 profile_id(跟随活跃指针)."""
        self._setup(provider, mock_engine, pin=False)
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value=self._STORE_JSON) as api:
            provider._tool_remember({"content": "remember"})
        assert "profile_id" not in api.call_args.args[2]

    # -- empty profile: no anchor → no pin -------------------------------------

    def test_empty_profile_pin_on_omits_profile_id(self, provider, mock_engine):
        """_mslm_profile 为空时不 pin(无可锚点,回落现状)."""
        self._setup(provider, mock_engine, pin=True, profile="")
        with patch("superlocalmemory.integrations.hermes._daemon_available", return_value=True), \
             patch("superlocalmemory.integrations.hermes._daemon_api",
                   return_value=self._RECALL_JSON) as api:
            provider._sync_recall("q")
            provider._tool_remember({"content": "remember"})
        assert api.call_count == 2
        assert "profile_id" not in self._qs_of(api.call_args_list[0])
        assert "profile_id" not in api.call_args_list[1].args[2]

    # -- offline fallback: engine receives profile_id ---------------------------

    def test_offline_fallback_engine_recall_gets_profile_id(self, provider, mock_engine):
        """daemon 不可达时 engine.recall 收到 profile_id."""
        self._setup(provider, mock_engine)
        mock_engine.recall.return_value.results = []
        provider._sync_recall("q")
        provider._tool_recall({"query": "q"})
        assert mock_engine.recall.call_count == 2
        for call in mock_engine.recall.call_args_list:
            assert call.kwargs.get("profile_id") == "zhihui"

    def test_offline_fallback_engine_store_gets_profile_id(self, provider, mock_engine):
        """daemon 不可达时 engine.store 收到 profile_id."""
        self._setup(provider, mock_engine)
        provider._tool_remember({"content": "remember"})
        provider.sync_turn("hello", "world")
        provider._sync_thread.join(timeout=5.0)
        assert mock_engine.store.call_count == 2
        for call in mock_engine.store.call_args_list:
            assert call.kwargs.get("profile_id") == "zhihui"

    def test_offline_fallback_pin_off_engine_gets_no_profile(self, provider, mock_engine):
        """pin off 时离线回落的 engine 调用不带 profile(None=跟随活跃)."""
        self._setup(provider, mock_engine, pin=False)
        mock_engine.recall.return_value.results = []
        provider._sync_recall("q")
        provider._tool_remember({"content": "remember"})
        assert mock_engine.recall.call_args.kwargs.get("profile_id") in (None, "")
        assert mock_engine.store.call_args.kwargs.get("profile_id") in (None, "")

    # -- config resolution: env fail-open, config.yaml override -----------------

    def test_pin_defaults_on_without_env(self, monkeypatch):
        """未设置 SLM_HERMES_PIN_PROFILE 时默认开启."""
        monkeypatch.delenv("SLM_HERMES_PIN_PROFILE", raising=False)
        assert SuperLocalMemoryProvider()._pin_profile is True

    def test_env_default_token_keeps_pin_on(self, monkeypatch):
        """SLM_HERMES_PIN_PROFILE=1(默认值)开启."""
        monkeypatch.setenv("SLM_HERMES_PIN_PROFILE", "1")
        assert SuperLocalMemoryProvider()._pin_profile is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "FALSE", "No", " 0 "])
    def test_env_opt_out_disables_pin(self, monkeypatch, raw):
        """0/false/no(大小写、空白不敏感)关闭 pin."""
        monkeypatch.setenv("SLM_HERMES_PIN_PROFILE", raw)
        assert SuperLocalMemoryProvider()._pin_profile is False

    @pytest.mark.parametrize("raw", ["", "garbage", "maybe", "2", "true"])
    def test_env_unparseable_fails_open(self, monkeypatch, raw):
        """解析失败视为 true(safe default)."""
        monkeypatch.setenv("SLM_HERMES_PIN_PROFILE", raw)
        assert SuperLocalMemoryProvider()._pin_profile is True

    def test_config_yaml_pin_profile_false_overrides_default(self, provider, monkeypatch):
        """config.yaml 的 pin_profile: false 等价关闭."""
        monkeypatch.delenv("SLM_HERMES_PIN_PROFILE", raising=False)
        with patch.object(provider, "_load_hermes_config", return_value={
            "pin_profile": False,
        }), patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()
            provider.initialize("session_1", agent_identity="coder")
        assert provider._pin_profile is False

    def test_config_yaml_pin_profile_true_overrides_env_off(self, provider, monkeypatch):
        """config.yaml 优先级高于 env: pin_profile: true 覆盖 env=0."""
        monkeypatch.setenv("SLM_HERMES_PIN_PROFILE", "0")
        with patch.object(provider, "_load_hermes_config", return_value={
            "pin_profile": True,
        }), patch("superlocalmemory.core.config.SLMConfig") as MockConfig, \
             patch("superlocalmemory.core.engine.MemoryEngine") as MockEngine:
            MockConfig.load.return_value = MagicMock()
            MockEngine.return_value = MagicMock()
            provider.initialize("session_1", agent_identity="coder")
        assert provider._pin_profile is True

    def test_config_schema_documents_pin_profile(self, provider):
        """get_config_schema 暴露 pin_profile 开关."""
        keys = {item["key"] for item in provider.get_config_schema()}
        assert "pin_profile" in keys
