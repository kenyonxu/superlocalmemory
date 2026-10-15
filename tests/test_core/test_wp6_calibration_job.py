"""Run the real job orchestration with local generator/worker protocol fakes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from superlocalmemory.core.answer_calibration import CalibrationJob
from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.retrieval.judge_calibration_file import measured_thresholds
from superlocalmemory.retrieval.judge_recipe import RECIPE_V1
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult


def _engine(root):
    facts = [
        AtomicFact(
            fact_id=str(i),
            content=f"Service {i}: Alice Chen approved the checklist.",
            embedding=[1.0, 0.0],
        )
        for i in range(32)
    ]
    judge = SimpleNamespace(
        backend="laya",
        ready=True,
        _model="local-pinned-fixture",
        top_k=3,
        _recipe=RECIPE_V1,
        _timeout_s=1.5,
        threshold=0.6,
    )
    judge._background_exchange = lambda req, timeout: {
        "ok": True,
        "probabilities": [0.1 if "AbsentProject" in req["query"] else 0.9],
    }
    channel = Mock()
    channel.search.return_value = [("0", 0.9)]
    db = Mock()
    db.get_all_facts.return_value = facts
    db.get_facts_by_ids.return_value = [facts[0]]
    retrieval = SimpleNamespace(_sufficiency_judge=judge, _semantic=channel)
    config = SimpleNamespace(
        base_dir=root,
        active_profile="default",  # the real SLMConfig field; it has no profile_id
        embedding=EmbeddingConfig(),
        retrieval=SimpleNamespace(min_semantic_evidence=0.6),
    )
    engine = SimpleNamespace(
        profile_id="default",
        _config=config,
        _db=db,
        _retrieval_engine=retrieval,
        _embedder=SimpleNamespace(embed=lambda text: [1.0, 0.0]),
        recall=lambda query, **kw: RecallResponse(
            results=[
                RetrievalResult(
                    next((f for f in facts if f"Service {f.fact_id}?" in query), facts[0])
                )
            ]
        ),
    )

    retrieval.recall = lambda query, profile, **kw: engine.recall(query, **kw)
    return engine


def test_job_writes_thresholds_progress_and_uses_only_local_model(tmp_path, monkeypatch):
    from superlocalmemory.core import answer_check_memo, calibration_examples, recall_gate

    seen = []

    def generate(model, fact, absent_subject="", stop=None):
        assert recall_gate.is_background_work()
        seen.append(model)
        return {
            "question": f"Who approved the checklist for {absent_subject or fact.split(':')[0]}?",
            "answer_quote": "Alice Chen",
        }

    monkeypatch.setattr(calibration_examples, "local_question", generate)
    monkeypatch.setattr(answer_check_memo, "clear", lambda judge: None)
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    engine = _engine(tmp_path)
    job = CalibrationJob()
    job.start(engine)
    job._thread.join(5)
    assert not job._thread.is_alive()
    assert job.status()["state"] == "complete", job.status()
    assert job.status()["done"] == 48
    assert (
        measured_thresholds(tmp_path / "answer_check_calibration.json")[
            ("laya", "sufficiency-set-v2")
        ]
        == 0.9
    )
    assert engine._retrieval_engine._sufficiency_judge._recipe.recipe_id == "sufficiency-set-v2"
    assert set(seen) == {"llama3.2:latest"}
    job.stop()


def test_unavailable_judge_and_cpu_model_cannot_start(tmp_path):
    engine = _engine(tmp_path)
    job = CalibrationJob()
    with pytest.raises(ValueError, match="GPU"):
        job.start(engine, model="llama3.2-cpu")
    engine._retrieval_engine._sufficiency_judge.ready = False
    with pytest.raises(ValueError, match="warm"):
        job.start(engine)
    assert not (tmp_path / "answer_check_calibration.json").exists()


def test_daemon_stops_calibration_before_closing_engine():
    from superlocalmemory.server.live_engine import engine_to_close

    job = Mock()
    engine = object()
    assert (
        engine_to_close(SimpleNamespace(engine=engine, answer_calibration_job=job), None) is engine
    )
    job.stop.assert_called_once()


def test_replaced_engine_cannot_publish_after_the_last_sample(tmp_path, monkeypatch):
    from superlocalmemory.core import calibration_examples, calibration_measure

    engine = _engine(tmp_path)
    holder = {"engine": engine}
    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: [])

    def measure(*a):
        holder["engine"] = object()
        return {"judge": {"threshold": 0.6}, "answered": 32, "unanswered": 16}

    monkeypatch.setattr(calibration_measure, "measure_examples", measure)
    job = CalibrationJob()
    job.start(engine, current_engine=lambda: holder["engine"])
    job._thread.join(2)
    assert job.status()["state"] == "failed"
    assert not (tmp_path / "answer_check_calibration.json").exists()


def test_a_rejected_duplicate_start_cannot_rebind_the_running_job(tmp_path):
    engine = _engine(tmp_path)
    job = CalibrationJob()
    job._stop.bind(engine)
    job._status["state"] = "running"
    with pytest.raises(ValueError, match="already running"):
        job.start(_engine(tmp_path), current_engine=lambda: object())
    assert job._stop._engine is engine


def test_shared_answer_quote_cannot_label_an_unretrieved_source_as_supported(tmp_path):
    from superlocalmemory.core.calibration_control import CancelToken
    from superlocalmemory.core.calibration_measure import _sample

    engine = _engine(tmp_path)
    engine.recall = lambda *a, **kw: RecallResponse(
        results=[RetrievalResult(AtomicFact(fact_id="0", content="Alice Chen approved Service 0."))]
    )
    token = CancelToken()
    token.bind(engine)
    example = {
        "question": "Who approved Service 31?",
        "answer": "Alice Chen",
        "fact_id": "31",
        "answered": True,
    }
    _, probability, _ = _sample(engine, engine._retrieval_engine._sufficiency_judge, example, token)
    assert probability is None


def test_a_cancelled_run_can_be_retried_until_daemon_shutdown(tmp_path, monkeypatch):
    from superlocalmemory.core import calibration_examples

    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: [])
    job = CalibrationJob()
    job._stop.set()
    job._status["state"] = "failed"
    old_token = job._stop
    job.start(_engine(tmp_path))
    job._thread.join(2)
    assert job._stop is not old_token
    job.stop()
    with pytest.raises(ValueError, match="stopping"):
        job.start(_engine(tmp_path))


def test_non_discriminating_calibration_cannot_replace_working_thresholds():
    from superlocalmemory.core.calibration_measure import _fit

    with pytest.raises(ValueError, match="held-out"):
        _fit("fixture::768", [0.6] * 32, [0.6] * 16, [], [], 0)


def test_calibration_never_uses_the_llm_enabled_public_recall_path(tmp_path):
    from superlocalmemory.core.calibration_control import CancelToken
    from superlocalmemory.core.calibration_measure import _sample
    from superlocalmemory.storage.models import Mode

    engine = _engine(tmp_path)
    fact = engine._db.get_all_facts.return_value[0]
    seen = []

    def local(query, profile, *, mode, limit):
        seen.append(mode)
        return RecallResponse(results=[RetrievalResult(fact)])

    engine._retrieval_engine.recall = local
    engine.recall = Mock(side_effect=AssertionError("Public recall may invoke cloud LLM/learning"))
    token = CancelToken()
    token.bind(engine)
    _sample(
        engine,
        engine._retrieval_engine._sufficiency_judge,
        {
            "question": "Who approved Service 0?",
            "answer": "Alice Chen",
            "fact_id": "0",
            "answered": True,
        },
        token,
    )
    assert seen == [Mode.A]
    engine.recall.assert_not_called()


def test_local_calibration_refuses_a_cloud_embedding_provider(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    from dataclasses import replace

    from superlocalmemory.core import calibration_examples

    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: [])
    engine._config.embedding = replace(engine._config.embedding, provider="openai")
    with pytest.raises(ValueError, match="local embedding"):
        CalibrationJob().start(engine)


def test_semantic_floor_uses_the_answer_source_not_a_higher_scoring_noise_hit(tmp_path):
    import math

    from superlocalmemory.core.calibration_control import CancelToken
    from superlocalmemory.core.calibration_measure import _sample

    engine = _engine(tmp_path)
    source = AtomicFact(
        fact_id="0",
        content="Alice Chen approved the checklist.",
        embedding=[0.55, math.sqrt(1 - 0.55**2)],
    )
    noise = AtomicFact(fact_id="noise", content="random deployment identifiers", embedding=[1, 0])
    engine._db.get_facts_by_ids.return_value = [source, noise]
    engine._retrieval_engine._semantic.search.return_value = [("noise", 0.99)]
    engine._retrieval_engine.recall = lambda *a, **kw: RecallResponse(
        results=[RetrievalResult(source)]
    )
    token = CancelToken()
    token.bind(engine)
    _, _, scores = _sample(
        engine,
        engine._retrieval_engine._sufficiency_judge,
        {
            "question": "Who approved the checklist?",
            "answer": "Alice Chen",
            "fact_id": "0",
            "answered": True,
        },
        token,
    )
    assert scores == pytest.approx([0.55])


@pytest.mark.parametrize("provider,backend", [("ollama", ""), ("", " OPENAI ")])
def test_local_calibration_refuses_actual_remote_ollama_or_normalized_reranker(
    tmp_path, provider, backend, monkeypatch
):
    from dataclasses import replace

    from superlocalmemory.core import calibration_examples

    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: [])
    engine = _engine(tmp_path)
    engine._config.embedding = replace(
        engine._config.embedding,
        provider=provider,
        ollama_base_url="https://remote.example" if provider else "http://127.0.0.1:11434",
    )
    engine._config.retrieval.cross_encoder_backend = backend
    engine._config.retrieval.cross_encoder_endpoint = "https://remote.example"
    with pytest.raises(ValueError, match="local"):
        CalibrationJob().start(engine)
