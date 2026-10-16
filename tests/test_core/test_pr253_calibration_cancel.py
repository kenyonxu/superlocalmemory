"""PR-253 #6: cancel one run, including nested waits, without stopping the service."""
import threading
import time

from superlocalmemory.core.answer_calibration import CalibrationJob
from superlocalmemory.core.calibration_control import CancelToken
from superlocalmemory.core.recall_gate import background_work, begin_recall, end_recall
from superlocalmemory.retrieval.judge_recipe import JudgeDocument
from superlocalmemory.retrieval.laya_set_recipe import calibration_probability
from superlocalmemory.retrieval.sufficiency import LayaSufficiencyJudge
from tests.test_core.test_wp6_calibration_job import _engine


def test_exact_review_cancel_interrupts_nested_foreground_and_busy_judge_wait():
    token = CancelToken()
    judge = LayaSufficiencyJudge(start=False)
    result, started = [], threading.Event()
    begin_recall()
    judge._lock.acquire()

    def run():
        with background_work(preempt_requested=token.is_set):
            started.set()
            result.append(calibration_probability(
                judge, "Who approved?", [JudgeDocument("Alice.")]))

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(1)
    time.sleep(0.03)
    token.set()
    worker.join(0.3)
    stopped_before_foreground_released = not worker.is_alive()
    try:
        assert stopped_before_foreground_released
        assert result == [None]
    finally:
        end_recall()
        judge._lock.release()
        worker.join(1)
        judge.shutdown()


def test_public_cancel_preserves_calibration_and_allows_next_run(tmp_path, monkeypatch):
    from superlocalmemory.core import calibration_examples
    began, release = threading.Event(), threading.Event()

    def build(engine, model, samples, stop):
        began.set()
        while not stop.is_set():
            stop.wait(0.01)
        return []

    monkeypatch.setattr(calibration_examples, "build_examples", build)
    engine = _engine(tmp_path)
    path = tmp_path / "answer_check_calibration.json"
    path.write_text("previous calibration bytes")
    job = CalibrationJob()
    try:
        job.start(engine)
        assert began.wait(1)
        job.cancel()
        job._thread.join(0.5)
        assert job.status()["state"] == "cancelled"
        assert path.read_text() == "previous calibration bytes"
        monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: [])
        job.start(engine)
        job._thread.join(0.5)
        assert not job._shutdown.is_set()
    finally:
        release.set()
        job.stop()


def test_cancel_closes_outstanding_local_generator_connection(tmp_path, monkeypatch):
    import socket

    from superlocalmemory.core import calibration_examples
    opened = threading.Event()
    left, right = socket.socketpair()

    class Connection:
        sock = left
        def __init__(self, *a, **kw):
            pass
        def request(self, *a):
            pass
        def getresponse(self):
            opened.set()
            left.recv(1)
            raise OSError("generator connection cancelled")
        def close(self):
            left.close()

    monkeypatch.setattr(calibration_examples, "local_ollama_connection", Connection)
    job = CalibrationJob()
    try:
        job.start(_engine(tmp_path))
        assert opened.wait(1)
        job.cancel()
        job._thread.join(0.5)
        assert not job._thread.is_alive()
        assert job.status()["state"] == "cancelled"
        assert not (tmp_path / "answer_check_calibration.json").exists()
    finally:
        job.stop()
        left.close()
        right.close()


def test_cancelling_background_reply_wait_preserves_foreground_worker():
    import os

    from superlocalmemory.retrieval.laya_background import read_line
    from superlocalmemory.retrieval.sufficiency import LineReader
    token = CancelToken()
    read_fd, write_fd = os.pipe()
    result = []

    def wait():
        with background_work(preempt_requested=token.is_set):
            result.append(read_line(LineReader(read_fd), time.monotonic() + 10))

    worker = threading.Thread(target=wait)
    worker.start()
    time.sleep(0.02)
    token.set()
    worker.join(0.3)
    try:
        assert not worker.is_alive()
        assert result == [None]
        os.write(write_fd, b'{"id":1,"ok":true}\n')  # pipe still belongs to the live judge
    finally:
        os.close(read_fd)
        os.close(write_fd)
        worker.join(1)


def test_cancel_immediately_before_publication_keeps_previous_bytes(tmp_path, monkeypatch):
    from superlocalmemory.core import answer_calibration, calibration_examples, calibration_measure
    from superlocalmemory.core.calibration_guard import judge_identity
    engine = _engine(tmp_path)
    judge = engine._retrieval_engine._sufficiency_judge
    path = tmp_path / "answer_check_calibration.json"
    path.write_bytes(b"previous bytes")
    measured = calibration_measure._fit("fixture::768", [0.9] * 28, [0.1] * 14, [], [], 0)
    measured["baseline"]["judge_identity"] = judge_identity(judge)
    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: [])
    monkeypatch.setattr(calibration_measure, "measure_examples", lambda *a: measured)
    job = CalibrationJob()
    original = answer_calibration._entry

    def cancelling_entry(judge, measured):
        job.cancel()
        return original(judge, measured)

    monkeypatch.setattr(answer_calibration, "_entry", cancelling_entry)
    try:
        job.start(engine)
        job._thread.join(1)
        assert job.status()["state"] == "cancelled"
        assert path.read_bytes() == b"previous bytes"
    finally:
        job.stop()
