"""Fidelity failure metadata must survive JSON and remain visible in text."""

import json
from argparse import Namespace
from unittest.mock import patch

import pytest

from superlocalmemory.cli import commands
from superlocalmemory.server.recall_serializer import recall_response_metadata
from superlocalmemory.storage.models import RecallResponse


@pytest.mark.parametrize("status", ["degraded", "unavailable"])
@pytest.mark.parametrize("command", ["recall", "trace"])
@pytest.mark.parametrize("use_json", [False, True])
@pytest.mark.parametrize("has_results", [False, True])
def test_daemon_fidelity_diagnostics(capsys, status, command, use_json, has_results):
    metadata = recall_response_metadata(RecallResponse(source_fidelity_status=status))
    result = {
        "results": [{"content": "verified source", "score": 0.5}] if has_results else [],
        "no_confident_match": not has_results,
        **metadata,
    }
    args = Namespace(query="approval", limit=10, json=use_json)
    with (
        patch("superlocalmemory.cli.daemon.is_daemon_running", return_value=True),
        patch("superlocalmemory.cli.daemon.daemon_request", return_value=result),
    ):
        getattr(commands, "cmd_" + command)(args)
    out = capsys.readouterr().out
    if use_json:
        data = json.loads(out)["data"]
        assert data["source_fidelity_status"] == status
        assert data["source_fidelity_note"] == metadata["source_fidelity_note"]
    else:
        assert metadata["source_fidelity_note"] in out
        assert "slm doctor" in out
        assert "No confident match." not in out


@pytest.mark.parametrize("status", ["degraded", "unavailable"])
def test_direct_trace_text_reports_fidelity(capsys, status):
    response = RecallResponse(source_fidelity_status=status)
    with (
        patch("superlocalmemory.cli.daemon.is_daemon_running", return_value=False),
        patch("superlocalmemory.core.config.SLMConfig.load"),
        patch("superlocalmemory.core.engine.MemoryEngine") as engine,
    ):
        engine.return_value.recall.return_value = response
        commands.cmd_trace(Namespace(query="approval", limit=10, json=False))
    out = capsys.readouterr().out
    assert recall_response_metadata(response)["source_fidelity_note"] in out
    assert "slm doctor" in out
