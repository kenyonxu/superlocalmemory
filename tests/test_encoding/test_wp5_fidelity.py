"""Extraction must preserve the source's people, quotations and assertions."""

import json
from unittest.mock import Mock

import pytest

from superlocalmemory.core.config import EncodingConfig
from superlocalmemory.encoding.fact_extractor import _SYSTEM_PROMPT, FactExtractor
from superlocalmemory.encoding.source_fidelity import check_fact_against_source
from superlocalmemory.storage.models import AtomicFact, Mode

QUOTE = '[Alex Morgan]: Priya said "I am leaving tomorrow", but I will stay.'
TURNS = "[Alex Morgan]: I use Python for payroll. [Alexa Morgan]: I use Rust for payroll."


def extract(source, mode=Mode.A, response=None):
    llm = Mock() if mode is not Mode.A else None
    if llm:
        llm.is_available.return_value = True
        llm.generate.return_value = json.dumps(response)
    config = EncodingConfig(enable_entity_reflexion=False)
    return FactExtractor(config, llm=llm, mode=mode).extract_facts(
        [source], session_id="test", session_date="2026-03-14"
    )


def test_quote_is_never_rewritten():
    facts = extract(QUOTE)
    assert facts
    assert all('"I am leaving tomorrow"' in f.content for f in facts)
    assert any("Alex Morgan will stay" in f.content for f in facts)


def test_later_speaker_is_not_attributed_to_first():
    facts = extract(TURNS)
    assert any("Alexa Morgan" in f.content and "Rust" in f.content for f in facts)
    assert not any("Alex Morgan" in f.content and "Rust" in f.content for f in facts)


@pytest.mark.parametrize(
    "source",
    [
        'Priya said "I\'ve finished my review". I have not finished mine.',
        "Priya said “I am leaving tomorrow”. I will stay.",
        "Priya said \"She told me 'I am leaving' yesterday\". I will stay.",
    ],
)
def test_nested_and_curly_quotes_survive(source):
    facts = extract(source)
    assert all(
        "the user" not in f.content.split("said", 1)[-1].split('".', 1)[0]
        for f in facts
        if "said" in f.content and '"' in f.content
    )


def test_first_person_only_rewrites_user():
    facts = extract("Assistant: I recommend Rust.\nUser: I use Python for my tools.")
    assert any("the user" in f.content and "Python" in f.content for f in facts)
    assert not any("the user" in f.content and "Rust" in f.content for f in facts)


@pytest.mark.parametrize(
    ("source", "fact"),
    [
        (
            "def charge(amount): return amount * 100",
            "Alexa Morgan works at Google as a software engineer",
        ),
        ("I use Python for payroll.", "The user prefers Python over Rust."),
        ("My vitamin D was 18 ng/mL.", "Alice's vitamin D was 18 ng/mL."),
        (
            "I prefer morning meetings, never on Fridays.",
            "The user prefers morning meetings on Fridays.",
        ),
        ("The cluster has 12 nodes.", "The cluster has 13 nodes."),
        ("The cache is enabled.", "The cache is not enabled."),
        (QUOTE, 'Priya said "Alex Morgan is leaving tomorrow", but Alex Morgan will stay.'),
        (TURNS, "Alex Morgan use Rust for payroll."),
        ("My daughter Mira starts school.", "Mira's daughter starts school."),
    ],
)
def test_guard_rejects_unsupported_assertion(source, fact):
    assert not check_fact_against_source(fact, source).ok


def test_prompt_has_no_example_people():
    assert "Alice" not in _SYSTEM_PROMPT
    assert "Google" not in _SYSTEM_PROMPT
    assert "1-5" in _SYSTEM_PROMPT


@pytest.mark.parametrize("mode", [Mode.B, Mode.C])
def test_model_inventions_are_rejected_at_extractor(mode):
    facts = extract(
        "def charge(amount): return amount * 100",
        mode,
        [
            {
                "text": "Alexa Morgan works at Google as a software engineer",
                "entities": ["Alexa Morgan", "Google"],
                "fact_type": "semantic",
            },
        ],
    )
    assert not any("Google" in f.content for f in facts)


def test_extraction_caps_and_collapses_near_duplicates():
    extractor = FactExtractor(EncodingConfig())
    facts = extractor._deduplicate(
        [
            AtomicFact(content="The user prefers morning meetings."),
            AtomicFact(content="The user prefers morning meetings!"),
            AtomicFact(content="The user never prefers morning meetings."),
        ]
    )
    assert len(facts) == 2
    # The configured cap (4.2.5): a hard-coded five overrode it from 11 Oct.
    cap = EncodingConfig().max_facts_per_chunk
    assert len(extract(" ".join(f"The relay uses channel {i}." for i in range(20)))) <= cap


def test_long_llm_input_is_bounded():
    llm = Mock()
    llm.is_available.return_value = True
    llm.generate.return_value = "[]"
    FactExtractor(
        EncodingConfig(enable_entity_reflexion=False), llm=llm, mode=Mode.B
    ).extract_facts(["Line of source content. " * 4000], session_id="test")
    assert llm.generate.call_count
    assert all(len(call.kwargs["prompt"]) < 9000 for call in llm.generate.call_args_list)


def test_entity_grounding_is_required_even_when_reflexion_is_disabled():
    facts = extract(
        "Priya works on payroll.",
        Mode.B,
        [
            {
                "text": "Priya works on payroll.",
                "fact_type": "semantic",
                "entities": ["Priya", "Google"],
            },
        ],
    )
    assert facts and "Google" not in facts[0].entities


def test_near_duplicates_collapse_without_merging_numbers_or_polarity():
    facts = FactExtractor._deduplicate(
        [
            AtomicFact(content="The user prefers morning meetings."),
            AtomicFact(content="Morning meetings are preferred by the user."),
            AtomicFact(content="The user never prefers morning meetings."),
            AtomicFact(content="The relay uses channel 4."),
            AtomicFact(content="The relay uses channel 9."),
        ]
    )
    assert len(facts) == 4


def test_named_subject_cannot_swap_object_in_one_turn():
    source = "Alex owns the blue bike. Alexa owns the red bike."
    assert not check_fact_against_source("Alex owns the red bike.", source).ok


@pytest.mark.parametrize(
    ("source", "fact"),
    [
        ("Alex manages Priya.", "Priya manages Alex."),
        ("I prefer Python, not Rust.", "The user prefers Rust, not Python."),
    ],
)
def test_guard_detects_relation_and_negated_object_swaps(source, fact):
    assert not check_fact_against_source(fact, source).ok


def test_sentence_split_does_not_break_quotes():
    source = '[Alex]: Priya said "I am leaving. I will call." I will stay.'
    facts = extract(source)
    assert any('"I am leaving. I will call."' in f.content for f in facts)


@pytest.mark.parametrize(
    ("source", "fact"),
    [
        ("The cache is enabled.", "The cache is disabled."),
        ("The service supports IPv6.", "IPv6 is unsupported by the service."),
    ],
)
def test_implicit_negation_cannot_flip_a_positive_source(source, fact):
    assert not check_fact_against_source(fact, source).ok


@pytest.mark.parametrize(
    "source",
    [
        "[Alex]: Priya said 'I've finished my review', but I will stay.",
        '[Alex]: Priya said "I am leaving\nI will call later", but I will stay.',
    ],
)
def test_contractions_and_multiline_quotations_keep_their_speaker(source):
    facts = extract(source)
    assert facts
    assert all("Priya said" not in f.content or "Alex has finished" not in f.content for f in facts)
    assert any(
        "I've finished" in f.content or "I am leaving\nI will call later" in f.content
        for f in facts
    )


@pytest.mark.parametrize(
    ("source", "actor"),
    [
        ("I use Rust for payroll.", "the user"),
        ("Assistant: I use Rust for payroll.", "Assistant"),
    ],
)
def test_model_first_person_uses_the_actual_speaker(source, actor):
    facts = extract(
        source,
        Mode.B,
        [{"text": "I use Rust for payroll.", "fact_type": "semantic", "entities": []}],
    )
    assert any(actor in f.content for f in facts)
    if actor == "Assistant":
        assert not any("the user" in f.content for f in facts)


def test_model_fragments_are_not_atomic_facts():
    facts = extract(
        "Ship the iOS build before the Android build.",
        Mode.B,
        [
            {"text": "iOS build", "fact_type": "semantic"},
            {"text": "Android build", "fact_type": "semantic"},
        ],
    )
    assert not any(f.content in ("iOS build", "Android build") for f in facts)


def test_explicit_user_subject_cannot_replace_assistant_subject():
    assert not check_fact_against_source(
        "The user uses Rust for payroll.", "Assistant: I use Rust for payroll."
    ).ok


def test_exact_source_span_must_not_end_inside_a_number():
    source = "The Kestrel checkpoint was recalled at rank 1 in 2004.6 ms."
    assert not check_fact_against_source(
        "The Kestrel checkpoint was recalled at rank 1 in 2004.", source
    ).ok


def test_redundant_partial_service_facts_collapse():
    facts = FactExtractor._deduplicate(
        [
            AtomicFact(content="Its next service is in January 2027 at 43,800 km."),
            AtomicFact(content="Its next service is in January 2027."),
            AtomicFact(content="Its next service is at 43,800 km."),
        ]
    )
    assert len(facts) == 1
    assert "2027" in facts[0].content and "43,800" in facts[0].content


def test_conditional_code_expressions_are_not_runtime_facts():
    source = (
        "def charge(amount):\n    if amount <= 0:\n"
        '        raise ValueError("amount must be positive")\n    return amount * 100'
    )
    facts = extract(
        source,
        Mode.B,
        [
            {"text": "amount <= 0", "fact_type": "semantic"},
            {"text": "amount * 100", "fact_type": "semantic"},
            {"text": "at rank 1", "fact_type": "semantic"},
        ],
    )
    assert not any(f.content in ("amount <= 0", "amount * 100", "at rank 1") for f in facts)


def test_duplicate_words_do_not_merge_different_actors():
    facts = FactExtractor._deduplicate(
        [
            AtomicFact(content="The user prefers tea."),
            AtomicFact(content="Alex prefers tea."),
        ]
    )
    assert len(facts) == 2


def test_declared_turn_speakers_are_applied_before_chunking():
    facts = FactExtractor(EncodingConfig()).extract_facts(
        ["I use Python for payroll.", "I use Rust for payroll."],
        session_id="test",
        speaker_a="Alex Morgan",
        speaker_b="Alexa Morgan",
    )
    assert any("Alex Morgan" in f.content and "Python" in f.content for f in facts)
    assert any("Alexa Morgan" in f.content and "Rust" in f.content for f in facts)
    assert not any("Alex Morgan" in f.content and "Rust" in f.content for f in facts)
