"""Strict maintenance requests preserve actionable worker errors."""

import pytest

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.runtimes import worker_client
from superlocalmemory.runtimes.managed_text import ManagedTextEmbedder


def test_strict_request_reports_failure_while_normal_request_stays_fail_open(tmp_path):
    class Broken:
        def embed_texts(self, texts, *, prompt):
            raise worker_client.MediaWorkerError('Not enough free memory: need 3400 MB.')

        def stop(self):
            pass

    emb = ManagedTextEmbedder(EmbeddingConfig(provider='slm-media'), data_root=tmp_path,
                             client_supplier=Broken)
    assert emb.embed('memory') is None
    with pytest.raises(worker_client.MediaWorkerError, match='need 3400 MB'):
        emb.embed_batch_strict(['memory'])


def test_load_lock_timeout_is_distinguished_from_low_memory(client_factory, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def timeout(*a, **kw):
        raise RuntimeError('ram_reservation(media-model-load) timeout after 600.0s')
        yield

    monkeypatch.setattr(worker_client.ram_lock, 'ram_reservation', timeout)
    client = client_factory()
    with pytest.raises(worker_client.MediaWorkerError, match='another model load') as exc:
        client.embed_texts(['memory'], prompt='Document')
    assert '600' in str(exc.value)
    assert 'Try again' in str(exc.value)


def test_worker_load_error_kind_is_reported(client_factory, monkeypatch):
    client = client_factory()
    monkeypatch.setattr(client, '_roundtrip', lambda *a, **kw: {
        'ok': False, 'error': 'request failed (ImportError)'})
    with pytest.raises(worker_client.MediaWorkerError, match='ImportError') as exc:
        client.embed_texts(['memory'], prompt='Document')
    assert 'slm media enable' in str(exc.value)
