"""CLI contract for the transport-neutral Living Brain truth model."""

from __future__ import annotations

import json
from argparse import Namespace
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from superlocalmemory.cli.commands import cmd_brain


def test_brain_json_serializes_the_shared_truth_model(tmp_path: Path) -> None:
    """The CLI must expose the same canonical snapshot as the MCP boundary."""
    captured = StringIO()
    snapshot = {
        "contract": "superlocalmemory.brain-truth/v1",
        "profile_id": "alpha",
        "generated_at": "2026-08-16T00:00:00Z",
        "control_plane": "observation_only",
        "memory_activity": {"availability": "available", "facts_total": 5},
        "feedback": {"availability": "available", "signals_total": 2},
        "agent_experience": {
            "availability": "available",
            "claimed_experiences_total": 1,
            "independently_verified_experiences_total": 0,
            "cognitive_turns_total": 1,
            "cognitive_turns_by_state": {"finalized": 1},
        },
        "external_evidence": {
            "availability": "available",
            "receipts_total": 1,
            "control_plane": "observation_only",
        },
        "correction_quality": {"availability": "available", "cases_total": 0},
    }
    with patch("superlocalmemory.core.config.SLMConfig.load") as load:
        config = MagicMock()
        config.active_profile = "alpha"
        load.return_value = config
        with patch(
            "superlocalmemory.brain.truth.BrainTruthService.snapshot", return_value=snapshot
        ):
            with patch("builtins.print", side_effect=lambda text: captured.write(str(text) + "\n")):
                cmd_brain(Namespace(json=True))

    envelope = json.loads(captured.getvalue())
    assert envelope["success"] is True
    assert envelope["command"] == "brain"
    assert envelope["data"] == snapshot


def test_brain_text_calls_observations_observations_not_learning(tmp_path: Path) -> None:
    """Human output must not imply that receipts alter recall or ranking."""
    snapshot = {
        "contract": "superlocalmemory.brain-truth/v1",
        "profile_id": "alpha",
        "generated_at": "2026-08-16T00:00:00Z",
        "control_plane": "observation_only",
        "memory_activity": {"availability": "available", "facts_total": 5},
        "feedback": {"availability": "available", "signals_total": 2},
        "agent_experience": {
            "availability": "available",
            "claimed_experiences_total": 1,
            "independently_verified_experiences_total": 0,
            "cognitive_turns_total": 1,
            "cognitive_turns_by_state": {"finalized": 1},
        },
        "external_evidence": {
            "availability": "available",
            "receipts_total": 1,
            "control_plane": "observation_only",
        },
        "correction_quality": {"availability": "available", "cases_total": 0},
    }
    captured = StringIO()
    with patch("superlocalmemory.core.config.SLMConfig.load") as load:
        config = MagicMock()
        config.active_profile = "alpha"
        load.return_value = config
        with patch(
            "superlocalmemory.brain.truth.BrainTruthService.snapshot", return_value=snapshot
        ):
            with patch("builtins.print", side_effect=lambda text: captured.write(str(text) + "\n")):
                cmd_brain(Namespace(json=False))

    output = captured.getvalue().lower()
    assert "observation only" in output
    assert "ranking" in output
    assert "model routing" in output
    assert "training" not in output
