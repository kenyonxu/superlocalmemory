"""Outcome idempotency keys pass remote binding without selecting a profile."""

from tests.test_security.test_remote_profile_binding import READ_KEY, WRITE_KEY, _run


def test_remote_outcome_report_id_keeps_key_profile_binding():
    _, answer, stub, runtime = _run("report_outcome", {
        "memory_ids": "fact-test", "outcome": "success", "report_id": "report-test",
    }, WRITE_KEY, active="personal")
    assert answer["result"]["isError"] is False
    assert stub.reached[0]["params"]["arguments"]["report_id"] == "report-test"
    assert stub.reached[0]["params"]["arguments"]["profile_id"] == "work"
    assert runtime.snapshot.profile_id == "personal"


def test_remote_outcome_report_id_cannot_select_another_profile():
    _, answer, stub, _ = _run("report_outcome", {
        "memory_ids": "fact-test", "outcome": "success", "report_id": "report-test",
        "profile_id": "personal",
    }, WRITE_KEY)
    assert answer["result"]["isError"] is True
    assert not stub.reached


def test_read_key_cannot_submit_outcome_with_report_id():
    _, answer, stub, _ = _run("report_outcome", {
        "memory_ids": "fact-test", "outcome": "success", "report_id": "report-test",
    }, READ_KEY)
    assert answer["result"]["isError"] is True
    assert not stub.reached
