# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Soft prompts are injected into the model's context on every turn.

That makes an empty one worse than none: it spends the budget and asserts
something false about the user. Measured on a live store, the two prompts
actually being injected were:

    "The user's preferred technology stack includes: test, gate, practices,
     compliance, projects, while, their, processing, data."

    "The user typically When general workflow, frequently uses Bash (324
     times...); When when using up-stdio__get_image, ..."

The first names no technology. The second contains real signal wrapped in
broken grammar. Both are covered below by their exact live text, because a test
written against a tidied-up paraphrase is a test against a case that never
happened.
"""

from __future__ import annotations

import pytest

from superlocalmemory.parameterization.soft_prompt_generator import (
    _KNOWN_TECHNOLOGIES,
    _fix_stutter,
    _is_substantive,
)

# Verbatim from the live store, 2026-08-22.
_LIVE_TECH = (
    "test, gate, practices, compliance, projects, while, their, "
    "processing, data"
)
_LIVE_WORKFLOW = (
    "The user typically When general workflow, frequently uses Bash "
    "(324 times, 20% of all tool usage); When when using "
    "up-stdio__get_image, typically follow with up-http__echo_text"
)


# ---------------------------------------------------------------------------
# What counts as saying something
# ---------------------------------------------------------------------------

def test_the_prompt_that_was_actually_being_injected_is_rejected():
    assert not _is_substantive("tech_preference", {"technologies": _LIVE_TECH})


def test_a_real_stack_is_kept():
    assert _is_substantive(
        "tech_preference", {"technologies": "python, postgres, docker"})


def test_one_real_technology_among_noise_is_enough():
    """The filter removes prompts that name nothing, not prompts that are messy."""
    assert _is_substantive(
        "tech_preference", {"technologies": "compliance, gate, postgres"})


def test_generic_words_alone_do_not_count_as_naming_a_technology():
    """"tool", "api" and "framework" are why prose gets classified as tech.

    They are in the vocabulary that DETECTS a technology preference, which is
    the reason ordinary sentences end up in this category at all. Treating them
    as evidence that a claim names a technology would keep every one of those.
    """
    assert not _is_substantive(
        "tech_preference", {"technologies": "tool, api, framework"})


def test_nothing_substituted_means_nothing_to_say():
    assert not _is_substantive("tech_preference", {"technologies": ""})
    assert not _is_substantive("identity", {})


def test_a_stopword_list_would_not_have_caught_this():
    """Why the check is a vocabulary of what a technology IS.

    Every term in the live failure is an ordinary English word. "compliance" is
    indistinguishable from "postgres" by any property except membership of the
    set of technologies, so no exclusion list separates them.
    """
    for word in ("gate", "practices", "compliance"):
        assert word not in _KNOWN_TECHNOLOGIES
        # ...and it is not a word anyone would put on a stopword list either.
        assert len(word) > 3


def test_other_categories_use_a_length_floor_not_the_tech_vocabulary():
    assert _is_substantive(
        "workflow_pattern",
        {"workflow_description": "frequently uses Bash (324 times)"})
    assert not _is_substantive("workflow_pattern", {"workflow_description": "x"})


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------

def test_the_duplicated_conjunction_is_removed():
    fixed = _fix_stutter(_LIVE_WORKFLOW)
    assert "When when" not in fixed
    assert "typically When" not in fixed


def test_the_real_signal_survives_the_grammar_fix():
    """The workflow prompt is worth keeping — it is only badly worded."""
    fixed = _fix_stutter(_LIVE_WORKFLOW)
    assert "Bash" in fixed
    assert "324 times" in fixed
    assert "up-http__echo_text" in fixed


def test_a_correctly_worded_prompt_is_left_alone():
    clean = "The user prefers concise responses. Avoid preamble."
    assert _fix_stutter(clean) == clean


# ---------------------------------------------------------------------------
# The gate this task was measured against
# ---------------------------------------------------------------------------

def test_counting_stored_prompts_measures_version_history_not_learning():
    """Why "more than ten stored templates" cannot be the measure.

    Storing a prompt deactivates the previous one for that category and inserts
    a new version. So the row count rises by one per category on every
    consolidation cycle, forever, whether or not anything was learned. On the
    live store it read 34 — which was two categories times seventeen cycles,
    with exactly two rows active and both of them junk.

    Asserted against the injector's own SQL so that if the versioning changes,
    this reasoning is revisited rather than silently left behind.
    """
    import inspect

    from superlocalmemory.parameterization import prompt_injector

    src = inspect.getsource(prompt_injector)
    assert "SET active = 0" in src, (
        "prompts are no longer versioned by deactivate-then-insert; the "
        "row-count reasoning recorded here needs rechecking"
    )
    assert "INSERT INTO soft_prompt_templates" in src


# ---------------------------------------------------------------------------
# The root cause behind the live failure
# ---------------------------------------------------------------------------

def test_a_frequent_word_is_not_a_technology_preference():
    """The actual defect, at the line that caused it.

    Word-frequency topics were mapped straight onto the technology-preference
    category, so "the subjects you write about a lot" was rendered as "the tools
    you have chosen". On a live store that produced a claim about a technology
    stack made entirely of ordinary English words.

    The values were never the problem — this user really does write about
    compliance and gates and test practices constantly. The claim about them
    was false.
    """
    from superlocalmemory.parameterization.pattern_extractor import (
        _BEHAVIORAL_TYPE_MAP,
    )

    assert _BEHAVIORAL_TYPE_MAP["interest"] != "tech_preference", (
        "frequent words are being asserted as the user's tooling again"
    )
    assert _BEHAVIORAL_TYPE_MAP["interest"] == "topic_interest"
    # The genuine tooling signal keeps its category. A live store holds correct
    # rows of this kind — Node.js, Go, Git, pip — beside the noisy ones, so the
    # fix must not collapse the two.
    assert _BEHAVIORAL_TYPE_MAP["entity_pref"] == "tech_preference"


def test_the_new_category_states_only_what_is_known():
    """It says the subjects recur. It does not infer a preference from that."""
    from superlocalmemory.parameterization.soft_prompt_generator import (
        CATEGORY_PRIORITY_ORDER,
        CATEGORY_TEMPLATES,
    )

    template = CATEGORY_TEMPLATES["topic_interest"]
    rendered = template.format(topics="compliance, gates, test practices")
    assert "prefer" not in rendered.lower()
    assert "stack" not in rendered.lower()
    assert "default to these" not in rendered.lower()
    assert "compliance" in rendered
    # Reachable: a category absent from the priority order is never rendered.
    assert "topic_interest" in CATEGORY_PRIORITY_ORDER


def test_the_new_category_is_enabled_out_of_the_box():
    """Otherwise the patterns are re-categorised into somewhere nothing reads."""
    from superlocalmemory.core.config import ParameterizationConfig

    assert "topic_interest" in ParameterizationConfig().categories_enabled


def test_pronouns_and_subordinators_cannot_become_interests():
    """"their" and "while" were recorded as interests at confidence 1.0.

    They reached that state because the miner filtered against a shorter
    stopword list than the one this codebase already maintained elsewhere.
    """
    from superlocalmemory.learning.pattern_miner_constants import STOPWORDS

    for word in ("their", "while", "these", "which", "however"):
        assert word in STOPWORDS, f"{word!r} can still become a recorded interest"


def test_real_subjects_still_get_through():
    """A stopword list that swallows the signal is worse than none."""
    from superlocalmemory.learning.pattern_miner_constants import STOPWORDS

    for word in (
        "compliance", "practices", "processing", "migration", "retrieval",
        "postgres", "embedding", "quarantine",
    ):
        assert word not in STOPWORDS, f"{word!r} is being filtered out as noise"


def test_the_two_stopword_lists_have_not_drifted_apart_again():
    """The miner reads the longer list rather than restating it.

    Two independent lists of the same thing is how one of them ends up missing
    the words that matter, which is exactly what happened.
    """
    from superlocalmemory.core.topic_signature import _STOPWORDS as shared
    from superlocalmemory.learning.pattern_miner_constants import STOPWORDS

    missing = set(shared) - set(STOPWORDS)
    assert not missing, (
        f"the miner no longer covers {len(missing)} words the shared list does: "
        f"{sorted(missing)[:8]}"
    )
