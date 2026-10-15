"""Source-rule confidence and incomplete recall must remain truthful on all callers."""
from superlocalmemory.core.answer_check_notice import answered_line
from superlocalmemory.retrieval.answer_check_status import answer_check_note
from superlocalmemory.server.recall_serializer import recall_response_metadata
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult


def test_new_source_rule_notices_keep_model_probability_as_model_probability():
    for suffix in ("explicit-approver-v2", "requested-slot-v2"):
        note = answered_line({"calibration_id": "laya:fixture+" + suffix}.get, 0.1)
        assert "stated explicitly" in note
        assert "model's own confidence" in note


def test_budget_notice_without_retrieval_context_does_not_claim_complete_results():
    assert "results are complete" not in answer_check_note("skipped", "budget")


def test_channel_timeout_never_gets_a_complete_results_notice():
    response = RecallResponse(results=[RetrievalResult(AtomicFact(content="Alice approved."))],
                              answer_check_status="skipped", answer_check_detail="budget",
                              channel_status={"semantic": "timeout", "bm25": "ok"})
    assert "results are complete" not in recall_response_metadata(response)["answer_check_note"]
    response.channel_status["semantic"] = "ok"
    assert "results are complete" in recall_response_metadata(response)["answer_check_note"]
