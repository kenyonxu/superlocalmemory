"""CLI scope propagation tests.

Covers the three paths in cmd_remember():
- daemon path (daemon_request POST /remember)
- pending path (store_pending with metadata)
- sync path (engine.store with scope/shared_with)

All tests use mocks — no real daemon or engine needed.
"""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.cli.commands import cmd_remember


def _make_args(
    content: str = "test content",
    tags: str = "",
    scope: str = "personal",
    shared_with: str = "",
    sync_mode: bool = False,
    json: bool = False,
) -> Namespace:
    """Build a minimal argparse Namespace for cmd_remember."""
    return Namespace(
        content=content,
        tags=tags,
        scope=scope,
        shared_with=shared_with,
        sync_mode=sync_mode,
        json=json,
    )


# ---------------------------------------------------------------------------
# Daemon path
# ---------------------------------------------------------------------------


def test_remember_cli_scope_global_daemon():
    """slm remember --scope global → daemon_request receives scope=global."""
    args = _make_args(scope="global")

    with patch(
        "superlocalmemory.cli.daemon.is_daemon_running",
        return_value=True,
    ):
        with patch(
            "superlocalmemory.cli.daemon.daemon_request",
            return_value={"fact_ids": ["f1"], "count": 1},
        ) as mock_req:
            cmd_remember(args)

    mock_req.assert_called_once()
    call_body = mock_req.call_args[0][2]
    assert call_body["scope"] == "global"
    assert call_body["shared_with"] == ""


def test_remember_cli_scope_shared_daemon():
    """slm remember --scope shared --shared-with a1,a2 → daemon_request receives shared_with."""
    args = _make_args(scope="shared", shared_with="a1,a2")

    with patch(
        "superlocalmemory.cli.daemon.is_daemon_running",
        return_value=True,
    ):
        with patch(
            "superlocalmemory.cli.daemon.daemon_request",
            return_value={"fact_ids": ["f1"], "count": 1},
        ) as mock_req:
            cmd_remember(args)

    call_body = mock_req.call_args[0][2]
    assert call_body["scope"] == "shared"
    assert call_body["shared_with"] == "a1,a2"


# ---------------------------------------------------------------------------
# Pending path (daemon not running)
# ---------------------------------------------------------------------------


def test_remember_cli_scope_global_pending():
    """slm remember --scope global (daemon down) → pending metadata contains scope."""
    args = _make_args(scope="global")

    with patch(
        "superlocalmemory.cli.daemon.is_daemon_running",
        return_value=False,
    ):
        with patch(
            "superlocalmemory.cli.daemon.ensure_daemon",
            return_value=False,
        ):
            with patch(
                "superlocalmemory.cli.pending_store.store_pending",
                return_value=42,
            ) as mock_store:
                cmd_remember(args)

    mock_store.assert_called_once()
    call_kwargs = mock_store.call_args.kwargs
    assert call_kwargs["metadata"]["scope"] == "global"


def test_remember_cli_scope_shared_pending():
    """slm remember --scope shared --shared-with a1,a2 → pending metadata contains shared_with list."""
    args = _make_args(scope="shared", shared_with="a1,a2")

    with patch(
        "superlocalmemory.cli.daemon.is_daemon_running",
        return_value=False,
    ):
        with patch(
            "superlocalmemory.cli.daemon.ensure_daemon",
            return_value=False,
        ):
            with patch(
                "superlocalmemory.cli.pending_store.store_pending",
                return_value=99,
            ) as mock_store:
                cmd_remember(args)

    call_kwargs = mock_store.call_args.kwargs
    assert call_kwargs["metadata"]["scope"] == "shared"
    assert call_kwargs["metadata"]["shared_with"] == ["a1", "a2"]


def test_remember_cli_default_scope_pending():
    """No --scope → personal; metadata should NOT contain scope (default omitted)."""
    args = _make_args(scope="personal")

    with patch(
        "superlocalmemory.cli.daemon.is_daemon_running",
        return_value=False,
    ):
        with patch(
            "superlocalmemory.cli.daemon.ensure_daemon",
            return_value=False,
        ):
            with patch(
                "superlocalmemory.cli.pending_store.store_pending",
                return_value=1,
            ) as mock_store:
                cmd_remember(args)

    call_kwargs = mock_store.call_args.kwargs
    assert "scope" not in call_kwargs["metadata"]
    assert "shared_with" not in call_kwargs["metadata"]


# ---------------------------------------------------------------------------
# Sync path (--sync)
# ---------------------------------------------------------------------------


def test_remember_cli_scope_global_sync():
    """slm remember --sync --scope global → engine.store receives scope=global."""
    args = _make_args(scope="global", sync_mode=True)

    mock_engine = MagicMock()
    mock_engine.store.return_value = ["f1", "f2"]

    with patch(
        "superlocalmemory.core.config.SLMConfig.load",
        return_value=MagicMock(),
    ):
        with patch(
            "superlocalmemory.core.engine.MemoryEngine",
            return_value=mock_engine,
        ):
            cmd_remember(args)

    mock_engine.store.assert_called_once()
    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs["scope"] == "global"
    assert call_kwargs["shared_with"] is None


def test_remember_cli_scope_shared_sync():
    """slm remember --sync --scope shared --shared-with a1,a2 → engine.store receives shared_with list."""
    args = _make_args(scope="shared", shared_with="a1,a2", sync_mode=True)

    mock_engine = MagicMock()
    mock_engine.store.return_value = ["f1"]

    with patch(
        "superlocalmemory.core.config.SLMConfig.load",
        return_value=MagicMock(),
    ):
        with patch(
            "superlocalmemory.core.engine.MemoryEngine",
            return_value=mock_engine,
        ):
            cmd_remember(args)

    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs["scope"] == "shared"
    assert call_kwargs["shared_with"] == ["a1", "a2"]


def test_remember_cli_default_scope_sync():
    """slm remember --sync (no --scope) → engine.store receives default scope=personal."""
    args = _make_args(scope="personal", sync_mode=True)

    mock_engine = MagicMock()
    mock_engine.store.return_value = ["f1"]

    with patch(
        "superlocalmemory.core.config.SLMConfig.load",
        return_value=MagicMock(),
    ):
        with patch(
            "superlocalmemory.core.engine.MemoryEngine",
            return_value=mock_engine,
        ):
            cmd_remember(args)

    call_kwargs = mock_engine.store.call_args.kwargs
    assert call_kwargs["scope"] == "personal"
    assert call_kwargs["shared_with"] is None


# ---------------------------------------------------------------------------
# argparse choices validation (integration with main.py)
# ---------------------------------------------------------------------------


def test_remember_cli_invalid_scope_argparse():
    """slm remember --scope invalid → argparse exits with error."""
    from superlocalmemory.cli.main import main

    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["slm", "remember", "hello", "--scope", "invalid"]):
            main()
    assert exc_info.value.code == 2
