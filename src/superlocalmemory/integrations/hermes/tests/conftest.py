"""pytest fixtures for SuperLocalMemoryProvider tests."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest


@pytest.fixture(autouse=True)
def _daemon_down_by_default():
    """Isolate tests from any live unified daemon on the dev machine.

    The provider's daemon-routing probes real machine state (PID file +
    HTTP). Tests default to 'daemon down' (in-process fallback); routing
    tests re-patch ``_daemon_available`` per test.
    """
    with patch(
        "superlocalmemory.integrations.hermes._daemon_available",
        return_value=False,
    ):
        yield


@pytest.fixture
def mock_slm_config():
    """Mock SLMConfig with basic attributes set."""
    config = MagicMock()
    config.active_profile = "default"
    config.mode = MagicMock()
    config.mode.name = "A"
    config.scope = MagicMock()
    config.scope.recall_include_global = False
    config.scope.recall_include_shared = False
    return config


@pytest.fixture
def mock_mode():
    """Mock Mode enum."""
    mode = MagicMock()
    mode.A = "A"
    return mode


@pytest.fixture
def mock_engine():
    """Mock MemoryEngine with async-ish initialize()."""
    engine = MagicMock()
    engine.initialize.return_value = None
    engine.store.return_value = ["fact_1", "fact_2"]
    engine.recall.return_value = MagicMock()
    engine.recall.return_value.results = []
    engine.recall.return_value.query_type = "factual"
    engine.recall.return_value.retrieval_time_ms = 100.0
    engine.close_session.return_value = 0
    engine.create_speaker_entities.return_value = None
    engine.db = MagicMock()
    return engine


@pytest.fixture
def provider():
    """Fresh SuperLocalMemoryProvider instance."""
    from superlocalmemory.integrations.hermes import SuperLocalMemoryProvider

    return SuperLocalMemoryProvider()


@pytest.fixture
def provider_with_mocks(provider, mock_slm_config, mock_engine):
    """Provider with SLMConfig and MemoryEngine pre-mocked."""
    from superlocalmemory.integrations.hermes import SuperLocalMemoryProvider

    # We'll patch at the method level in actual tests
    return provider
