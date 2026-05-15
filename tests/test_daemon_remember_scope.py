"""Daemon /remember endpoint scope propagation tests.

Covers sync (wait=True) and async (default) branches of the /remember
endpoint, plus Pydantic field validation for the scope field.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_engine():
    """Return a MagicMock engine with a minimal profile_id."""
    engine = MagicMock()
    engine.profile_id = "test_profile"
    engine.store.return_value = ["fact_001", "fact_002"]
    return engine


@pytest.fixture
def client(mock_engine):
    """Build a TestClient with get_engine_lazy patched.

    get_engine_lazy is imported as a local variable inside
    _register_daemon_routes (called by create_app), so we must
    patch BEFORE create_app() to intercept that local binding.
    """
    from superlocalmemory.server.unified_daemon import create_app

    with patch(
        "superlocalmemory.server.routes.helpers.get_engine_lazy",
        return_value=mock_engine,
    ):
        app = create_app()
        with TestClient(app) as tc:
            yield tc


# ---------------------------------------------------------------------------
# Scope field validation
# ---------------------------------------------------------------------------


def test_remember_scope_invalid(client):
    """POST /remember with an invalid scope → HTTP 422."""
    resp = client.post(
        "/remember",
        json={"content": "test", "scope": "invalid"},
    )
    assert resp.status_code == 422


def test_remember_default_scope(client, mock_engine):
    """POST /remember without scope → defaults to personal (sync branch)."""
    resp = client.post(
        "/remember?wait=true",
        json={"content": "hello world"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    mock_engine.store.assert_called_once()
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs.get("scope") == "personal"
    assert call_kwargs.get("shared_with") is None


# ---------------------------------------------------------------------------
# Sync branch (wait=True)
# ---------------------------------------------------------------------------


def test_remember_scope_global_sync(client, mock_engine):
    """POST /remember?wait=true with scope=global → engine.store receives scope=global."""
    resp = client.post(
        "/remember?wait=true",
        json={"content": "global knowledge", "scope": "global"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    mock_engine.store.assert_called_once()
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs.get("scope") == "global"
    assert call_kwargs.get("shared_with") is None


def test_remember_scope_shared_sync(client, mock_engine):
    """POST /remember?wait=true with scope=shared + shared_with → parsed list passed."""
    resp = client.post(
        "/remember?wait=true",
        json={
            "content": "shared secret",
            "scope": "shared",
            "shared_with": "agent1, agent2",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    mock_engine.store.assert_called_once()
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs.get("scope") == "shared"
    assert call_kwargs.get("shared_with") == ["agent1", "agent2"]


def test_remember_scope_shared_with_empty_sync(client, mock_engine):
    """Empty shared_with string → shared_with=None in sync branch."""
    resp = client.post(
        "/remember?wait=true",
        json={"content": "test", "scope": "shared", "shared_with": ""},
    )
    assert resp.status_code == 200
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs.get("shared_with") is None


# ---------------------------------------------------------------------------
# Async branch (default, wait=False)
# ---------------------------------------------------------------------------


def test_remember_scope_global_async(client):
    """POST /remember (async) with scope=global → pending metadata contains scope."""
    with patch(
        "superlocalmemory.cli.pending_store.store_pending",
        return_value=42,
    ) as mock_store_pending:
        resp = client.post(
            "/remember",
            json={"content": "async global", "scope": "global"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["pending_id"] == 42

        mock_store_pending.assert_called_once()
        call_args = mock_store_pending.call_args
        metadata = call_args.kwargs.get("metadata") or call_args.args[2]
        assert metadata.get("scope") == "global"


def test_remember_scope_shared_async(client):
    """POST /remember (async) with scope=shared + shared_with → metadata correct."""
    with patch(
        "superlocalmemory.cli.pending_store.store_pending",
        return_value=99,
    ) as mock_store_pending:
        resp = client.post(
            "/remember",
            json={
                "content": "async shared",
                "scope": "shared",
                "shared_with": "alpha, beta",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        mock_store_pending.assert_called_once()
        call_args = mock_store_pending.call_args
        metadata = call_args.kwargs.get("metadata") or call_args.args[2]
        assert metadata.get("scope") == "shared"
        assert metadata.get("shared_with") == ["alpha", "beta"]


def test_remember_default_scope_async(client):
    """POST /remember (async) without scope → metadata scope defaults to personal."""
    with patch(
        "superlocalmemory.cli.pending_store.store_pending",
        return_value=1,
    ) as mock_store_pending:
        resp = client.post(
            "/remember",
            json={"content": "default scope test"},
        )
        assert resp.status_code == 200

        mock_store_pending.assert_called_once()
        call_args = mock_store_pending.call_args
        metadata = call_args.kwargs.get("metadata") or call_args.args[2]
        assert metadata.get("scope") == "personal"
        assert "shared_with" not in metadata


def test_remember_async_preserves_existing_metadata(client):
    """Existing metadata dict is merged with scope/shared_with."""
    with patch(
        "superlocalmemory.cli.pending_store.store_pending",
        return_value=7,
    ) as mock_store_pending:
        resp = client.post(
            "/remember",
            json={
                "content": "merged meta",
                "scope": "global",
                "metadata": {"project": "slm", "tags": "important"},
            },
        )
        assert resp.status_code == 200

        mock_store_pending.assert_called_once()
        call_args = mock_store_pending.call_args
        metadata = call_args.kwargs.get("metadata") or call_args.args[2]
        assert metadata.get("scope") == "global"
        assert metadata.get("project") == "slm"
        assert metadata.get("tags") == "important"
