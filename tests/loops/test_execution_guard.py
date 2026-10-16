"""Secure copy refusal and descriptor lifetime stay fail closed."""

import os
from types import SimpleNamespace

import pytest

from superlocalmemory.loops import artifact_revision, private_workspace
from superlocalmemory.loops.artifact_revision import copy_inputs


def test_no_posix_timestamp_dependency(tmp_path, monkeypatch):
    work, copy = tmp_path / 'work', tmp_path / 'copy'
    work.mkdir()
    copy.mkdir()
    (work / 'state').write_text('fail')
    simulated = SimpleNamespace(**vars(os))
    simulated.name = 'nt'
    monkeypatch.setattr(private_workspace, 'os', simulated)
    assert private_workspace.platform_reason() is None
    # The copy uses the real filesystem backend, regardless of the host label.
    copy_inputs(work, ['state'], copy)
    assert (copy / 'state').read_text() == 'fail'


def test_mutation_after_copy_hash_does_not_change_copied_bytes(tmp_path, monkeypatch):
    work, copy = tmp_path / 'work', tmp_path / 'copy'
    work.mkdir()
    copy.mkdir()
    state = work / 'state'
    state.write_text('fail')
    original = artifact_revision.private_file
    streams = []

    def opened(path):
        stream = original(path)
        streams.append(stream)
        return stream

    monkeypatch.setattr(artifact_revision, 'private_file', opened)
    _, digest = copy_inputs(work, ['state'], copy)
    state.write_text('pass')
    assert (copy / 'state').read_text() == 'fail' and len(digest) == 64
    assert streams and all(stream.closed for stream in streams)


def test_disappearing_input_refuses_copy_and_closes_handles(tmp_path, monkeypatch):
    work, copy = tmp_path / 'work', tmp_path / 'copy'
    work.mkdir()
    copy.mkdir()
    state = work / 'state'
    state.write_text('fail')
    selected = artifact_revision._selected

    def disappear(*args):
        result = selected(*args)
        state.unlink()
        return result

    monkeypatch.setattr(artifact_revision, '_selected', disappear)
    with pytest.raises(ValueError, match='regular files'):
        copy_inputs(work, ['state'], copy)
    assert not list(copy.iterdir())


def test_nonregular_replacement_is_refused_without_blocking(tmp_path, monkeypatch):
    work, copy = tmp_path / 'work', tmp_path / 'copy'
    work.mkdir()
    copy.mkdir()
    state = work / 'state'
    state.write_text('fail')
    selected = artifact_revision._selected

    def replace_with_fifo(*args):
        result = selected(*args)
        state.unlink()
        os.mkfifo(state)
        return result

    monkeypatch.setattr(artifact_revision, '_selected', replace_with_fifo)
    with pytest.raises(ValueError, match='regular files'):
        copy_inputs(work, ['state'], copy)
    assert not list(copy.iterdir())
