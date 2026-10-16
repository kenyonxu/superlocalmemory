"""Protect the active calibration at publication, and define its commit point."""
from superlocalmemory.core.answer_calibration import CalibrationJob, write_calibration
from tests.test_core.test_wp6_calibration_job import _engine


def test_file_changed_during_measurement_is_retained_instead_of_overwritten(tmp_path, monkeypatch):
    from superlocalmemory.core import calibration_examples, calibration_measure
    engine = _engine(tmp_path)
    path = tmp_path / "answer_check_calibration.json"
    entry = {"backend": "laya", "recipe_id": "sufficiency-v1", "threshold": 0.6,
             "answered": 28, "unanswered": 14}
    write_calibration(tmp_path, entry)
    newer = []
    examples = [{"answered": True}] * 28 + [{"answered": False}] * 14
    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: examples)

    def sample(engine, judge, example, stop, *, baseline):
        write_calibration(tmp_path, {**entry, "threshold": 0.7})
        newer.append(path.read_bytes())
        baseline["kept"] = example["answered"]
        return example["answered"], 0.9 if example["answered"] else 0.1, []

    monkeypatch.setattr(calibration_measure, "_sample", sample)
    job = CalibrationJob()
    try:
        job.start(engine)
        job._thread.join(3)
        assert job.status()["state"] == "failed"
        assert path.read_bytes() == newer[-1]
    finally:
        job.stop()


def test_cancel_after_atomic_publication_returns_complete_instead_of_promising_previous_settings(
    tmp_path, monkeypatch,
):
    from superlocalmemory.core import calibration_examples
    from superlocalmemory.retrieval import laya_set_recipe
    engine = _engine(tmp_path)
    def generate(model, text, absent_subject="", **kwargs):
        return {"question": f"Who approved {absent_subject or text.split(':')[0]}?",
                "answer_quote": "Alice Chen"}

    monkeypatch.setattr(calibration_examples, "local_question", generate)
    job = CalibrationJob()
    states = []
    monkeypatch.setattr(laya_set_recipe, "refresh", lambda j: states.append(job.cancel()["state"]))
    try:
        job.start(engine)
        job._thread.join(3)
        assert states == ["complete"]
        assert not job._stop.cancel_requested
    finally:
        job.stop()


def test_local_generator_connection_is_built_only_by_the_fixed_loopback_gate(monkeypatch):
    import http.client
    from unittest.mock import Mock

    from superlocalmemory.core import outbound_http
    connect = Mock()
    monkeypatch.setattr(http.client, "HTTPConnection", connect)
    outbound_http.local_ollama_connection()
    connect.assert_called_once_with("127.0.0.1", 11434, timeout=30)
