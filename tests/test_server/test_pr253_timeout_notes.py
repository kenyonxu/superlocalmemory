"""PR-253 #7: timed-out retrieval never promises complete results."""
from types import SimpleNamespace

from superlocalmemory.retrieval.answer_check_status import answer_check_note
from superlocalmemory.server.recall_core import RecallCall
from superlocalmemory.server.recall_deadline import timed_out, unjudged


def test_exact_review_timeout_payload_does_not_claim_completeness():
    payload = timed_out(RecallCall("Who approved?", 3, "", "test", True),
                        SimpleNamespace(profile_id="default", generation=1))
    assert payload["results"] == []
    assert set(payload["channel_status"].values()) == {"timeout"}
    assert "results are complete" not in payload["answer_check_note"]
    assert "full results" in payload["answer_check_note"].lower()
    assert payload["answerability"] == "unjudged"


def test_lexical_fallback_explicitly_says_full_retrieval_was_unavailable():
    payload = unjudged({"results": [{"content": "Alice approved."}],
                        "degraded_reason": "recall_budget_exceeded"})
    assert "results are complete" not in payload["answer_check_note"]
    assert "keyword" in payload["answer_check_note"]
    assert payload["results"][0]["content"] == "Alice approved."


def test_true_check_only_skip_retains_complete_results_explanation():
    assert "results are complete" in answer_check_note("skipped", "budget", retrieval_complete=True)


def test_completed_retrieval_survives_slow_source_context_assembly(monkeypatch):
    import asyncio
    import time

    from superlocalmemory.server import recall_core
    from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult
    response = RecallResponse(results=[RetrievalResult(
        AtomicFact(fact_id="source", content="Alice approved the deployment."))])
    monkeypatch.setattr(recall_core, "effective_budget_s", lambda call: 0.08)
    monkeypatch.setattr(recall_core, "_engine_call", lambda *a: response)

    def slow(*a):
        time.sleep(0.15)
        return {"results": [{"content": "Alice approved the deployment."}]}

    monkeypatch.setattr(recall_core, "_envelope", slow)
    payload = asyncio.run(recall_core.run_recall(
        object(), RecallCall("Who approved?", 3, "", "test", True, skip_answer_check=True),
        app_state=SimpleNamespace()))
    assert payload["results"][0]["content"] == "Alice approved the deployment."
    assert payload["results"][0]["id"] == "source"
    assert payload["degraded_reason"] == "recall_assembly_budget_exceeded"
    assert "source context" in payload["answer_check_note"]
