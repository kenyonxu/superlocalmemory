"""Exact ENG-04 false support/refusal cases with recorded local probabilities."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from superlocalmemory.retrieval.sufficiency import LayaSufficiencyJudge


@pytest.mark.parametrize(
    "question,memory,probability,insufficient",
    [
        (
            "Which employee is allergic to peanuts?",
            "Joan Smith is allergic to peanuts.",
            0.1782,
            False,
        ),
        (
            "What is PostgREST used for?",
            "PostgREST exposes PostgreSQL tables as a RESTful HTTP API.",
            0.3435,
            False,
        ),
        (
            "What is the Kestrel CFO salary?",
            "The Kestrel CFO salary has not been recorded.",
            0.772,
            True,
        ),
        (
            "What is the CEO of Orchid called?",
            "The Orchid project lead is Priya Sen.",
            0.6868,
            True,
        ),
    ],
)
def test_explicit_requested_slot_beats_a_wrong_classifier(
    question, memory, probability, insufficient
):
    judge = LayaSufficiencyJudge(start=False)
    judge.threshold = 0.60
    judge._ready = True
    judge._exchange = Mock(return_value={"ok": True, "probabilities": [probability]})
    proc = SimpleNamespace(poll=lambda: None, stdin=True, stdout=True)
    outcome = judge._ask_locked(proc, question, [memory], 1e20)
    assert outcome.verdict.insufficient is insufficient
    assert outcome.verdict.probabilities == (probability,)
    judge.shutdown()


def test_a_denied_or_different_subject_never_becomes_extractively_supported():
    from superlocalmemory.retrieval.explicit_slots import supports_slot

    assert not supports_slot(
        "Which employee is allergic to peanuts?", "Joan Smith is not allergic to peanuts."
    )
    assert not supports_slot("What is PostgREST used for?", "Postgres exposes tables as HTTP APIs.")
    assert not supports_slot(
        "What is PostgREST used for?", "Someone said PostgREST exposes private tables."
    )


@pytest.mark.parametrize("verb", ["totals", "earns", "amounts to", "was set at"])
def test_a_later_scoped_salary_value_prevents_an_unknown_veto(verb):
    from superlocalmemory.retrieval.explicit_slots import rejects_slot

    memory = (
        f"The Kestrel CFO salary has not been recorded. The Kestrel CFO salary {verb} $140,000."
    )
    assert not rejects_slot("What is the Kestrel CFO salary?", [memory])


@pytest.mark.parametrize("frame", ["It is false that ", "It was denied that ", "Suppose ", '"'])
def test_denied_or_quoted_approval_cannot_override_the_judge(frame):
    from superlocalmemory.retrieval.explicit_answer import attribution_support

    assert not attribution_support(
        "Who approved the Beacon release?", frame + "Alice Chen approved the Beacon release."
    )


@pytest.mark.parametrize(
    "memory",
    [
        "Acme's chief executive is Alice Chen.",
        "Acme's C.E.O. is Alice Chen.",
        "The Acme project lead is Sam Ray. Acme's chief executive is Alice Chen.",
    ],
)
def test_lexical_absence_cannot_disprove_an_equivalent_explicit_ceo_answer(memory):
    from superlocalmemory.retrieval.explicit_slots import rejects_slot

    assert not rejects_slot("What is the CEO of Acme called?", [memory])


@pytest.mark.parametrize(
    "question", ["Has the Kestrel CFO salary been recorded?", "Is the Kestrel CFO salary known?"]
)
def test_explicit_unknown_answers_a_boolean_availability_question(question):
    from superlocalmemory.retrieval.explicit_slots import rejects_slot

    assert not rejects_slot(
        question, ["The Kestrel CFO salary has not been recorded; it is not known."]
    )


def test_conditional_approval_cannot_override_the_judge():
    from superlocalmemory.retrieval.explicit_answer import attribution_support

    assert not attribution_support(
        "Who approved the Beacon release?",
        "Alice Chen approved the Beacon release only if the tests passed.",
    )
