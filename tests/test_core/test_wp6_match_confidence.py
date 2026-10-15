from superlocalmemory.core.score_contract import finalize_score_contract
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult


def test_match_confidence_is_separate_from_fact_certainty():
    hit = RetrievalResult(
        AtomicFact(content="right", confidence=0.99), score=0.54, channel_scores={"semantic": 0.9}
    )
    noise = RetrievalResult(
        AtomicFact(content="noise", confidence=0.99), score=0.54, channel_scores={"semantic": 0.65}
    )
    response = finalize_score_contract(RecallResponse(results=[hit, noise]))
    assert response.results[0].match_confidence > response.results[1].match_confidence
    assert hit.memory_confidence == noise.memory_confidence == 0.99
    assert hit.confidence_basis == "raw_semantic_evidence"


def test_an_explicit_answer_does_not_relabel_the_classifier_probability():
    from superlocalmemory.core.answer_check_notice import answered_line

    values = {'calibration_id': 'local+explicit-approver-v1'}
    line = answered_line(values.get, 0.17)
    assert 'explicitly' in line
    assert 'model' in line and '0.17' in line
