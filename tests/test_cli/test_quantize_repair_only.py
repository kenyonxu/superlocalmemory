"""Sketch repair never runs precision transitions or starts an embedding engine."""

from argparse import Namespace
from unittest.mock import patch

import pytest

from superlocalmemory.cli.commands import cmd_quantize
from superlocalmemory.core.config import SLMConfig
from tests.test_dynamics.test_qjl_upgrade_repair import legacy_store as legacy_store


def _snapshot(db):
    return {
        table: [tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY 1')]
        for table in ('atomic_facts', 'fact_retention', 'polar_embeddings',
                      'embedding_quantization_metadata')
    }


@pytest.mark.parametrize('execute', [False, True])
def test_repair_only_leaves_precision_and_canonical_vectors_unchanged(
    legacy_store, execute, capsys,
):
    db, store, _, config = legacy_store
    # A full cycle WOULD reduce this fact from four bits to two bits.
    db.execute("UPDATE fact_retention SET retention_score=0.1 WHERE fact_id='repairable'")
    before = _snapshot(db)
    with patch.object(SLMConfig, 'load', return_value=config), patch(
        'superlocalmemory.core.engine.MemoryEngine',
        side_effect=AssertionError('repair must not build an engine'),
    ), patch('superlocalmemory.core.admission.gate_cli_mutation'):
        cmd_quantize(Namespace(repair_sketches=True, dry_run=not execute,
                               json=False, profile='default'))
    after = _snapshot(db)
    assert before['atomic_facts'] == after['atomic_facts']
    assert before['fact_retention'] == after['fact_retention']
    assert store.load('repairable', 'default', include_qjl=False).bit_width == 4
    if execute:
        assert store.load('repairable', 'default').qjl_bits.startswith(b'QJL1')
        # base geometry and bit width stay exactly as stored.
        assert [r[:4] + r[5:] for r in before['polar_embeddings']] == [
            r[:4] + r[5:] for r in after['polar_embeddings']]
    else:
        assert before == after
    output = capsys.readouterr().out
    assert 'repair' in output.lower() and '2' in output
    assert 'Downgrades:  0' in output


def test_full_execute_prints_counts_before_precision_is_changed(legacy_store, capsys):
    db, _, scheduler, config = legacy_store
    db.execute("UPDATE fact_retention SET retention_score=0.1 WHERE fact_id='repairable'")
    from superlocalmemory.dynamics.eap_scheduler import EAPScheduler
    original = EAPScheduler.run_eap_cycle

    def run(self, profile_id, *, dry_run=False):
        if not dry_run:
            output = capsys.readouterr().out
            assert 'Before applying' in output
            assert 'Downgrades:  1' in output
            assert 'QJL repair needed: 2' in output
            assert 'Upgrades:    0' in output and 'Forgotten:   0' in output
        return original(self, profile_id, dry_run=dry_run)

    from unittest.mock import MagicMock
    with patch.object(SLMConfig, 'load', return_value=config), patch(
        'superlocalmemory.core.engine.MemoryEngine',
        return_value=MagicMock(_db=db, profile_id='default'),
    ), patch('superlocalmemory.core.admission.gate_cli_mutation'), patch.object(
        EAPScheduler, 'run_eap_cycle', run,
    ):
        cmd_quantize(Namespace(repair_sketches=False, dry_run=False,
                               json=False, profile='default'))


def test_doctor_recommends_the_preview_and_the_safe_execute(legacy_store):
    from superlocalmemory.cli.doctor_quantization import qjl_finding

    db, _, _, config = legacy_store
    with patch.object(SLMConfig, 'load', return_value=config):
        _, _, fix = qjl_finding(db.db_path)
    assert 'slm quantize --repair-sketches' in fix
    assert 'slm quantize --repair-sketches --execute' in fix
    assert 'slm quantize --execute`' not in fix


def test_pending_dimension_switch_repairs_with_stored_geometry(legacy_store, capsys):
    from dataclasses import replace
    from pathlib import Path

    db, store, _, config = legacy_store
    rotation = Path(config.quantization.polar.rotation_matrix_path)
    before = rotation.read_bytes()
    config.embedding = replace(config.embedding, dimension=32)
    with patch.object(SLMConfig, 'load', return_value=config), patch(
        'superlocalmemory.core.admission.gate_cli_mutation',
    ):
        cmd_quantize(Namespace(repair_sketches=True, dry_run=False,
                               json=False, profile='default'))
    assert store.load('repairable', 'default').qjl_bits.startswith(b'QJL1')
    assert rotation.read_bytes() == before
    assert 'QJL repaired:      1' in capsys.readouterr().out


def test_human_repair_hint_preserves_the_requested_profile(legacy_store, capsys):
    _, _, _, config = legacy_store
    with patch.object(SLMConfig, 'load', return_value=config):
        cmd_quantize(Namespace(repair_sketches=True, dry_run=True,
                               json=False, profile='team one'))
    assert "slm quantize --repair-sketches --execute --profile 'team one'" in (
        capsys.readouterr().out)


def test_repair_refuses_incompatible_rotation_without_overwriting_it(legacy_store):
    from pathlib import Path

    import numpy as np

    db, _, _, config = legacy_store
    rotation = Path(config.quantization.polar.rotation_matrix_path)
    np.save(rotation, np.eye(32))
    before = rotation.read_bytes()
    snapshot = _snapshot(db)
    with patch.object(SLMConfig, 'load', return_value=config), patch(
        'superlocalmemory.core.admission.gate_cli_mutation',
    ), pytest.raises(ValueError, match='rotation matrix'):
        cmd_quantize(Namespace(repair_sketches=True, dry_run=False,
                               json=False, profile='default'))
    assert rotation.read_bytes() == before
    assert _snapshot(db) == snapshot


@pytest.mark.parametrize('dry_run', [True, False])
def test_repair_only_json_keeps_stdout_parseable_and_previews_before_apply(
    legacy_store, dry_run, capsys,
):
    import json

    _, _, _, config = legacy_store
    with patch.object(SLMConfig, 'load', return_value=config), patch(
        'superlocalmemory.core.admission.gate_cli_mutation',
    ):
        cmd_quantize(Namespace(repair_sketches=True, dry_run=dry_run,
                               json=True, profile='default'))
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload['success']
    assert payload['data']['repair_only'] and payload['data']['downgrades'] == 0
    assert payload['data']['plan']['repair_needed'] == 2
    assert payload['data']['repaired'] == (0 if dry_run else 1)
    assert '--repair-sketches --execute --json --profile default' in (
        payload['next_actions'][0]['command'])
    if not dry_run:
        assert 'Before applying' in output.err and 'QJL repair needed: 2' in output.err


def test_json_repair_failure_reports_rotation_cause_and_leaves_it_unchanged(legacy_store, capsys):
    import json
    from pathlib import Path

    import numpy as np

    db, _, _, config = legacy_store
    path = Path(config.quantization.polar.rotation_matrix_path)
    np.save(path, np.eye(32))
    before, snapshot = path.read_bytes(), _snapshot(db)
    with patch.object(SLMConfig, 'load', return_value=config), patch(
        'superlocalmemory.core.admission.gate_cli_mutation',
    ), pytest.raises(SystemExit) as exc:
        cmd_quantize(Namespace(repair_sketches=True, dry_run=False,
                               json=True, profile='default'))
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['error']['code'] == 'QJL_REPAIR_ERROR'
    assert 'Restore the original rotation matrix' in payload['error']['message']
    assert path.read_bytes() == before and _snapshot(db) == snapshot


def test_repair_reuses_legacy_polar_rotation_without_creating_turbo_rotation(
    legacy_store, capsys,
):
    from dataclasses import replace
    from pathlib import Path

    _, store, _, config = legacy_store
    source = Path(config.quantization.polar.rotation_matrix_path).read_bytes()
    polar = config.base_dir / 'polar_rotation_16.npy'
    turbo = config.base_dir / 'turbo_rotation_16.npy'
    polar.write_bytes(source)
    assert not turbo.exists()
    config.quantization = replace(config.quantization, polar=replace(
        config.quantization.polar, rotation_matrix_path='', codebook_method='turbo'))
    with patch.object(SLMConfig, 'load', return_value=config), patch(
        'superlocalmemory.core.admission.gate_cli_mutation',
    ):
        cmd_quantize(Namespace(repair_sketches=True, dry_run=False,
                               json=False, profile='default'))
    assert store.load('repairable', 'default').qjl_bits.startswith(b'QJL1')
    assert polar.read_bytes() == source and not turbo.exists()
    assert 'QJL repaired:      1' in capsys.readouterr().out
