"""PR-253 #3: a rejecting classifier cannot be overruled by a non-assertion."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from superlocalmemory.retrieval.sufficiency import LayaSufficiencyJudge

CASES = [
    ("Who approved the deployment?", "Alice Chen approved the deployment?"),
    ("Who is the CEO of Qualixar?", "Alice Chen is the CEO of Qualixar?"),
    ("What is AgentAssay used for?", "AgentAssay is used for."),
    ("What is AgentAssay used for?", "AgentAssay provides neither tests nor checks."),
    ("Who approved the deployment?",
     "The rejected draft read: 'Alice Chen approved the deployment.'"),
]


@pytest.mark.parametrize("question,memory", CASES)
def test_exact_review_nonanswers_keep_rejecting_judge_and_serialized_verdict(question, memory):
    judge = LayaSufficiencyJudge(start=False)
    judge.threshold, judge._ready = 0.6, True
    judge._exchange = Mock(return_value={"ok": True, "probabilities": [0.1]})
    proc = SimpleNamespace(poll=lambda: None, stdin=True, stdout=True)
    try:
        outcome = judge._ask_locked(proc, question, [memory], 1e20)
        assert outcome.verdict.rule_support == ()
        assert outcome.verdict.insufficient
        from superlocalmemory.server.recall_serializer import recall_response_metadata
        from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult
        response = RecallResponse(
            results=[RetrievalResult(AtomicFact(fact_id="source", content=memory))],
            abstained=outcome.verdict.insufficient, answer_check_status="judged",
        )
        assert recall_response_metadata(response)["answerability"] == "unsupported"
    finally:
        judge.shutdown()


def test_question_terminator_lost_at_the_reader_limit_cannot_create_support():
    from superlocalmemory.retrieval.judge_recipe import RECIPE_V1, JudgeDocument
    question = "Who approved the deployment?"
    statement = "Alice Chen approved the deployment?"
    original = "x" * (1801 - len(statement) - 2) + ". " + statement
    rendered = RECIPE_V1.render(JudgeDocument(original))
    assert len(rendered) == 1800 and original.endswith("?") and not rendered.endswith("?")
    judge = LayaSufficiencyJudge(start=False)
    judge.threshold, judge._ready = 0.6, True
    judge._exchange = Mock(return_value={"ok": True, "probabilities": [0.1]})
    try:
        outcome = judge._ask_locked(SimpleNamespace(poll=lambda: None), question, [rendered], 1e20)
        assert outcome.verdict.rule_support == ()
        assert outcome.verdict.insufficient
    finally:
        judge.shutdown()
