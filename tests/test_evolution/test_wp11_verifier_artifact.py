"""Reject malformed approval, and recover a verified artifact after restart."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from superlocalmemory.evolution.blind_verifier import parse_verification_response
from superlocalmemory.evolution.candidate_pipeline import CandidatePipeline
from superlocalmemory.evolution.evolution_store import EvolutionStore
from superlocalmemory.evolution.types import (
    EvolutionCandidate,
    EvolutionRecord,
    EvolutionStatus,
    EvolutionType,
    TriggerType,
)


@pytest.mark.parametrize("passed", ["false", "true", 1, None])
def test_verifier_requires_a_boolean(passed):
    result = parse_verification_response(json.dumps({"passed": passed, "confidence": 0.9}))
    assert result.passed is False


MALFORMED_APPROVALS = [
    '{"passed": "false", "confidence": "invalid", "reasoning": "approve"}',
    '{"passed":false,"confidence":"invalid","reasoning":"approve"}',
    '{"passed":true,"confidence":"invalid","reasoning":"approve"}',
    '{"passed":true,"issues":null,"reasoning":"approve"}',
    '{"passed":true,"issues":"approve"}',
    '{"passed":true,"issues":[{}]}',
    '{"passed":true,"confidence":true}',
    '{"passed":true,"confidence":NaN}',
    '{"passed":true,"confidence":2}',
    '{"passed":true,"reasoning":{}}',
    '{"result":{"passed":true,"reasoning":"approve"}}',
    '[{"passed":true}]',
    '{"passed":false,"passed":true}',
    '{"passed":true} {"passed":false,"reasoning":"Reject"}',
    '{"passed":true}}',
    '{"passed":true,"confidence":' + '9' * 1000 + '}',
    'I approve this skill. It looks good.',
    'The result is "passed": true based on my analysis.',
]


@pytest.mark.parametrize("response", MALFORMED_APPROVALS)
def test_malformed_approval_cannot_stage_or_activate(tmp_path, response):
    assert parse_verification_response(response).passed is False
    store = EvolutionStore(tmp_path / "memory.db")
    evolver = SimpleNamespace(
        _store=store, _read_skill_content=lambda _: "original",
        _llm_confirm=lambda *_: True, _generate_mutation=lambda _: "evolved",
        _extract_description=lambda _: "review",
        _blind_verify=lambda _: parse_verification_response(response),
        _write_evolved_skill=Mock(), _is_auto_approve=lambda: True,
        _get_activator=Mock(),
    )
    candidate = EvolutionCandidate(
        skill_name="review", trigger=TriggerType.DEGRADATION,
        evolution_type=EvolutionType.FIX,
    )
    pipeline = CandidatePipeline(evolver, candidate, "default")
    assert pipeline.run() == "rejected"
    assert store.get_latest_status(pipeline.record_id, "default") == EvolutionStatus.REJECTED
    evolver._write_evolved_skill.assert_not_called()
    evolver._get_activator.assert_not_called()


def test_artifact_directory_reconstructed_from_verified_transition(tmp_path):
    path = tmp_path / "memory.db"
    store = EvolutionStore(path)
    record = EvolutionRecord("r1", "review", None, EvolutionType.FIX, TriggerType.DEGRADATION)
    store.insert_record(record, "default")
    store.append_transition(
        "r1",
        "default",
        EvolutionStatus.CANDIDATE,
        EvolutionStatus.VERIFIED_QUARANTINED,
        metadata={"quarantine_dir_name": "review-v123"},
    )
    loaded = EvolutionStore(path).get_record("r1", "default")
    assert loaded.quarantine_dir_name == "review-v123"
    assert loaded.blind_verified is True
    assert EvolutionStore(path).get_record("r1", "other") is None
