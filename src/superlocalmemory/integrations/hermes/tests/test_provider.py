"""Tests for SuperLocalMemoryProvider — Chunk 1: skeleton + config parsing."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

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
