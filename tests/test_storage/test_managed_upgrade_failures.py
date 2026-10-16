"""Cold managed upgrades, actionable failures and an unchanged live vector space."""

from dataclasses import replace

import pytest

from superlocalmemory.core import embedding_reindex as er
from superlocalmemory.core import embedding_reindex_steps as steps
from superlocalmemory.runtimes.managed_text import ManagedTextEmbedder
from superlocalmemory.runtimes.worker_client import MediaWorkerClient, MediaWorkerError
from superlocalmemory.storage import embedding_spaces as sp
from tests.test_runtimes.conftest import StubEnv
from tests.test_storage.test_embedding_reindex_units import _target
from tests.test_storage.test_embedding_reindex_units import store as store


@pytest.fixture(autouse=True)
def _target_model_is_approved_for_text(monkeypatch):
    """These tests are about the re-index mechanics on a managed model. Whether a
    managed model may take text at all is tests/test_storage/test_every_switch_door_is_guarded.py."""
    from superlocalmemory.retrieval import support_recipe
    monkeypatch.setattr(support_recipe, "approved_for_text", lambda model: True)



def test_cold_managed_load_does_not_reserve_the_workers_ram_lock(store, monkeypatch):
    root, path = store
    target = replace(_target('fake:4', 4), provider='slm-media')
    client = MediaWorkerClient(StubEnv(root), model_id='fake:4', revision='',
                               load_timeout_s=5.0)
    embedder = ManagedTextEmbedder(target, data_root=root, client_supplier=lambda: client)
    monkeypatch.setattr(steps, 'build_embedder', lambda cfg: embedder)
    runner = er.ReindexRunner(db_path=path, data_root=root)
    try:
        assert runner._load_embedder(target) is embedder
        assert client.is_warm()
        assert len(embedder.embed('after cold load')) == 4
    finally:
        embedder.shutdown()


def test_failed_probe_keeps_worker_cause_and_closes_the_wrapper(store, monkeypatch):
    root, path = store
    target = replace(_target(), provider='slm-media')

    class RefusedClient:
        def embed_texts(self, texts, *, prompt):
            raise MediaWorkerError('Model load timed out after 600 seconds. Try again later.')

        def stop(self):
            pass

    embedder = ManagedTextEmbedder(target, data_root=root, client_supplier=RefusedClient)
    monkeypatch.setattr(steps, 'build_embedder', lambda cfg: embedder)
    runner = er.ReindexRunner(db_path=path, data_root=root)
    job = runner.request_switch(target)
    with sp.connect(path) as conn:
        before = [tuple(r) for r in conn.execute('SELECT * FROM fact_embeddings')]
        saved = conn.execute('SELECT * FROM embedding_reindex_jobs WHERE job_id=?',
                             (job['job_id'],)).fetchone()
    runner._run(dict(saved))
    failed = runner.status()
    assert failed['job']['state'] == 'failed'
    assert 'timed out after 600' in failed['job']['error']
    assert 'no vector' not in failed['job']['error']
    assert embedder.is_closed
    assert failed['live'] == 'old-model::8'
    with sp.connect(path) as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM fact_embeddings')] == before
    assert 'slm embedder switch' in failed['job']['retry_command']
    assert failed['job']['forget_command'] == 'slm embedder cancel'
    assert runner.cancel()['state'] == 'cancelled'
    assert runner.status()['live'] == 'old-model::8'
    assert runner.request_switch(target)['state'] == 'queued'


def test_uninstalled_managed_environment_names_the_repair_command(tmp_path):
    embedder = ManagedTextEmbedder(replace(_target(), provider='slm-media'),
                                  data_root=tmp_path, env=StubEnv(tmp_path, 'failed'))
    with pytest.raises(steps.StepFailed, match='slm media enable') as exc:
        steps.probe(embedder, 4)
    assert 'no vector' not in str(exc.value)


def test_empty_probe_answer_is_actionable():
    class Empty:
        def embed_batch(self, texts):
            return [None]

    with pytest.raises(steps.StepFailed, match='slm doctor') as exc:
        steps.probe(Empty(), 4)
    assert 'no vector' not in str(exc.value)


def test_retry_commands_keep_public_endpoints_and_hide_private_url_credentials(store):
    import json

    from superlocalmemory.storage.embedding_reindex_jobs import public_view

    root, path = store
    runner = er.ReindexRunner(db_path=path, data_root=root)
    target = replace(_target(), provider='openai', api_endpoint='http://127.0.0.1:50000/v1')
    job = runner.request_switch(target)
    with sp.connect(path) as conn:
        saved = dict(conn.execute('SELECT * FROM embedding_reindex_jobs WHERE job_id=?',
                                  (job['job_id'],)).fetchone())
    saved['state'] = 'failed'
    assert '--endpoint http://127.0.0.1:50000/v1' in public_view(saved)['retry_command']
    for endpoint in ('https://user:private-example@localhost/v1',
                     'https://localhost/v1?key=private-example'):
        saved['to_config'] = json.dumps({'model_name': 'new-model', 'dimension': 4,
                                        'provider': 'openai', 'api_endpoint': endpoint})
        view = public_view(saved)
        assert 'retry_note' in view and 'retry_command' not in view
        assert 'private-example' not in json.dumps(view)


@pytest.mark.parametrize("fail_probe", [False, True])
def test_cancelling_a_cold_probe_cannot_resurrect_the_job(store, monkeypatch, fail_probe):
    from tests.test_storage.test_embedding_reindex_units import FakeEmbedder

    root, path = store
    runner = er.ReindexRunner(db_path=path, data_root=root)
    job = runner.request_switch(_target())
    client = FakeEmbedder(4, 'new', fail_after=0 if fail_probe else None,
                          hook=lambda calls: runner.cancel() if calls == 1 else None)
    monkeypatch.setattr(steps, 'build_embedder', lambda cfg: client)
    with sp.connect(path) as conn:
        saved = dict(conn.execute('SELECT * FROM embedding_reindex_jobs WHERE job_id=?',
                                  (job['job_id'],)).fetchone())
    runner._run(saved)
    status = runner.status()
    assert status['job']['state'] == 'cancelled'
    assert status['live'] == 'old-model::8'
    assert client.closed
