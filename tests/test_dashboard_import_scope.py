"""Dashboard /api/import scope propagation tests.

Covers the import endpoint reading scope and shared_with from JSON
and passing them to engine.store().
"""

from __future__ import annotations

import json
import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_engine():
    """Return a MagicMock engine."""
    engine = MagicMock()
    engine.store.return_value = ["fact_001"]
    return engine


@pytest.fixture
def client(mock_engine):
    """Build a TestClient with engine patched to prevent real init."""
    from superlocalmemory.server.api import create_app

    # Patch the engine class and config BEFORE create_app so lifespan
    # initializes with our mock instead of trying real init.
    with patch(
        "superlocalmemory.core.engine.MemoryEngine",
        return_value=mock_engine,
    ):
        with patch(
            "superlocalmemory.core.config.SLMConfig.load",
            return_value=MagicMock(),
        ):
            app = create_app()
            with TestClient(app) as tc:
                yield tc


def _make_import_file(memories: list[dict]) -> tuple[str, io.BytesIO]:
    """Build a multipart file upload payload from a memories list."""
    data = {"version": "3.0.0", "memories": memories}
    buf = io.BytesIO(json.dumps(data).encode())
    return "memories.json", buf


# ---------------------------------------------------------------------------
# Scope propagation tests
# ---------------------------------------------------------------------------


def test_import_with_scope(client, mock_engine):
    """POST /api/import with scope=global → engine.store receives scope=global."""
    filename, buf = _make_import_file([
        {"content": "global knowledge", "scope": "global"},
    ])

    resp = client.post(
        "/api/import",
        files={"file": (filename, buf, "application/json")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["imported_count"] == 1
    assert data["errors"] == []

    mock_engine.store.assert_called_once()
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs.get("scope") == "global"
    assert call_kwargs.get("shared_with") is None


def test_import_with_shared_scope(client, mock_engine):
    """POST /api/import with scope=shared + shared_with → passed correctly."""
    filename, buf = _make_import_file([
        {
            "content": "shared secret",
            "scope": "shared",
            "shared_with": ["agent1", "agent2"],
        },
    ])

    resp = client.post(
        "/api/import",
        files={"file": (filename, buf, "application/json")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_count"] == 1

    mock_engine.store.assert_called_once()
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs.get("scope") == "shared"
    assert call_kwargs.get("shared_with") == ["agent1", "agent2"]


def test_import_default_scope(client, mock_engine):
    """POST /api/import without scope → defaults to personal."""
    filename, buf = _make_import_file([
        {"content": "default scope test"},
    ])

    resp = client.post(
        "/api/import",
        files={"file": (filename, buf, "application/json")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_count"] == 1

    mock_engine.store.assert_called_once()
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs.get("scope") == "personal"
    assert call_kwargs.get("shared_with") is None


def test_import_invalid_scope_skipped(client, mock_engine):
    """Invalid scope → recorded in errors, not imported, engine.store not called."""
    filename, buf = _make_import_file([
        {"content": "bad scope", "scope": "invalid"},
    ])

    resp = client.post(
        "/api/import",
        files={"file": (filename, buf, "application/json")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_count"] == 0
    assert data["total_processed"] == 1
    assert len(data["errors"]) == 1
    assert "invalid scope 'invalid'" in data["errors"][0]

    mock_engine.store.assert_not_called()


def test_import_mixed_valid_and_invalid_scope(client, mock_engine):
    """Mixed valid/invalid scopes → valid imported, invalid skipped with errors."""
    filename, buf = _make_import_file([
        {"content": "first", "scope": "global"},
        {"content": "second", "scope": "bad_scope"},
        {"content": "third", "scope": "personal"},
    ])

    resp = client.post(
        "/api/import",
        files={"file": (filename, buf, "application/json")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_count"] == 2
    assert data["total_processed"] == 3
    assert len(data["errors"]) == 1
    assert "invalid scope 'bad_scope'" in data["errors"][0]

    assert mock_engine.store.call_count == 2
    # First call
    assert mock_engine.store.call_args_list[0].kwargs["scope"] == "global"
    # Third call (second was skipped)
    assert mock_engine.store.call_args_list[1].kwargs["scope"] == "personal"
