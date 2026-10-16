"""Copied managed roots must never reuse the source root's executable or weights."""
import json
import shutil
from types import SimpleNamespace

import pytest

from superlocalmemory.core import laya_runtime as lr
from superlocalmemory.runtimes.managed_env import EnvSpec, LocalDirSource, ManagedEnv


def _runtime(root):
    venv = root / 'venv'
    (venv / 'bin').mkdir(parents=True)
    (venv / 'bin' / 'python').write_text('fake interpreter')
    (venv / 'pyvenv.cfg').write_text(f'home = /usr/bin\ncommand = /usr/bin/python -m venv {venv}\n')
    (venv / 'bin' / 'pip').write_text(f'#!{venv}/bin/python\n')
    (root / 'weights').mkdir()
    return venv / 'bin' / 'python'


def test_copied_laya_marker_never_probes_original_root(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    python = _runtime(source)
    record = {'python': str(python), 'model_path': str(source / 'weights'), 'verified': True}
    (source / '.slm-managed').write_text(json.dumps(record))
    copied = tmp_path / 'copy'
    shutil.copytree(source, copied)
    probes = []
    monkeypatch.setattr(lr, 'check_interpreter', lambda *a, **k: probes.append(a) or '')
    status = lr._on_disk(SimpleNamespace(), copied)
    assert status.state == lr.STATE_FAILED
    assert status.python == str(copied / 'venv' / 'bin' / 'python')
    assert probes == []


def test_managed_laya_marker_stores_relative_paths(tmp_path, monkeypatch):
    python = _runtime(tmp_path / 'laya')
    root = python.parent.parent.parent
    monkeypatch.setattr(lr, 'verify', lambda *a: (True, ''))
    lr._verify_and_record(root, python, root / 'cache', root / 'weights')
    record = json.loads((root / '.slm-managed').read_text())
    assert record['python'] == 'venv/bin/python'
    assert record['model_path'] == 'weights'
    assert record['hf_home'] == 'cache'


@pytest.mark.parametrize('metadata', ['command', 'shebang'])
def test_copied_media_runtime_refuses_execution(tmp_path, monkeypatch, metadata):
    source = tmp_path / 'source'
    _runtime(source)
    copied = tmp_path / 'copy'
    shutil.copytree(source, copied)
    if metadata == 'shebang':
        (copied / 'venv' / 'pyvenv.cfg').write_text('home = /usr/bin\n')
    spec = EnvSpec('media', (), LocalDirSource(source / 'weights', model_id='m', revision='r'), 1)
    env = ManagedEnv(spec, root=copied)
    calls = []
    monkeypatch.setattr('subprocess.run', lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError, match='repair'):
        env.run_script(copied / 'script.py')
    assert calls == []


def test_relative_laya_marker_reports_current_paths(tmp_path, monkeypatch):
    root = tmp_path / 'laya'
    _runtime(root)
    record = {'python': 'venv/bin/python', 'model_path': 'weights',
              'hf_home': 'cache', 'verified': True}
    (root / '.slm-managed').write_text(json.dumps(record))
    monkeypatch.setattr(lr, 'check_interpreter', lambda *a, **k: '')
    status = lr._on_disk(SimpleNamespace(), root)
    assert status.state == lr.STATE_READY
    assert status.python == str(root / 'venv' / 'bin' / 'python')
    assert status.model_path == str(root / 'weights')
    assert status.hf_home == str(root / 'cache')


def test_explicit_external_adoption_remains_supported(tmp_path, monkeypatch):
    external = tmp_path / 'external'
    python = _runtime(external)
    root = tmp_path / 'laya'
    root.mkdir()
    record = {'python': str(python), 'model_path': str(external / 'weights'), 'verified': True}
    (root / 'adopted.json').write_text(json.dumps(record))
    monkeypatch.setattr(lr, 'check_interpreter', lambda *a, **k: '')
    status = lr._on_disk(SimpleNamespace(), root)
    assert status.state == lr.STATE_READY
    assert not status.managed
    assert status.python == str(python)


def test_symlinked_managed_weights_cannot_escape_root(tmp_path, monkeypatch):
    root = tmp_path / 'laya'
    _runtime(root)
    external = tmp_path / 'foreign-weights'
    external.mkdir()
    (root / 'weights').rmdir()
    (root / 'weights').symlink_to(external, target_is_directory=True)
    record = {'python': 'venv/bin/python', 'model_path': 'weights', 'verified': True}
    (root / '.slm-managed').write_text(json.dumps(record))
    probes = []
    monkeypatch.setattr(lr, 'check_interpreter', lambda *a, **k: probes.append(a) or '')
    assert lr._on_disk(SimpleNamespace(), root).state == lr.STATE_FAILED
    assert not probes


def test_current_venv_in_path_with_spaces_is_not_marked_moved(tmp_path):
    from superlocalmemory.core.laya_paths import venv_refusal

    python = _runtime(tmp_path / 'data folder')
    assert venv_refusal(python.parent.parent) == ''


def test_configured_cache_cannot_override_managed_root_ownership(tmp_path, monkeypatch):
    root = tmp_path / 'laya'
    python = _runtime(root)
    (root / '.slm-managed').write_text(json.dumps({
        'python': 'venv/bin/python', 'model_path': 'weights', 'verified': True}))
    probes = []
    monkeypatch.setattr(lr, 'check_interpreter', lambda *a, **k: probes.append(a) or '')
    cfg = SimpleNamespace(sufficiency_python=str(python), sufficiency_model=str(root / 'weights'),
                          sufficiency_hf_home=str(tmp_path / 'foreign-cache'))
    assert lr._on_disk(cfg, root).state == lr.STATE_FAILED
    assert probes == []
