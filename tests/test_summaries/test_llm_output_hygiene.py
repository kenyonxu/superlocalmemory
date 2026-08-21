# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com

"""Mode B/C summaries must not show the model talking to itself.

WHY THIS EXISTS
---------------
A Mode B daily summary on the author's own store rendered in the dashboard as:

    I apologize for the previous confusion. It seems that I misunderstood the
    context of the texts provided.

    To provide a concise summary paragraph, here is a merge of all the key
    information:

    The SuperLocalMemory project has made significant progress ...

Two root causes, both fixed in 4.0.8 and both pinned here:

1. The Ollama call sent **no system prompt at all** while the cloud path sent
   one, so a chat-tuned local model answered in chat register.
2. Nothing sanitised the response, so the scaffolding reached the user.

The cleaner must also be *conservative*: it runs on every Mode B/C summary, so
a false positive silently deletes real content from someone's memory summary.
That direction of failure is worse than leaving an apology in, which is why the
"keeps legitimate content" cases below are not optional extras.
"""

from __future__ import annotations

import pytest

from superlocalmemory.summaries.base import (
    SUMMARY_SYSTEM_PROMPT,
    clean_llm_summary,
)

# The exact output observed in the dashboard, verbatim.
OBSERVED_SLOP = (
    "I apologize for the previous confusion. It seems that I misunderstood the "
    "context of the texts provided.\n\n"
    "To provide a concise summary paragraph, here is a merge of all the key "
    "information:\n\n"
    "The SuperLocalMemory project has made significant progress in addressing "
    "various issues, including bug fixes, testing, and new features.\n\n"
    "Let me know if you would like more detail!"
)


class TestStripsScaffolding:
    def test_observed_dashboard_slop_is_removed(self):
        out = clean_llm_summary(OBSERVED_SLOP)
        assert "apologi" not in out.lower()
        assert "misunderstood" not in out.lower()
        assert "here is a merge" not in out.lower()
        assert "let me know" not in out.lower()

    def test_observed_dashboard_slop_keeps_the_actual_summary(self):
        out = clean_llm_summary(OBSERVED_SLOP)
        assert "SuperLocalMemory project has made significant progress" in out

    @pytest.mark.parametrize(
        "preamble",
        [
            "Sure! Here is the summary:",
            "Certainly.",
            "Of course!",
            "Here's a concise summary of the notes:",
            "To summarise the recorded facts:",
            "Based on the facts provided,",
            "As an AI language model, I can summarise this.",
            "I'm sorry, I misread the earlier request.",
        ],
    )
    def test_known_preambles_are_dropped(self, preamble):
        body = "Varun shipped 4.0.7 and fixed the code bridge archive filter."
        assert clean_llm_summary(f"{preamble}\n\n{body}") == body

    @pytest.mark.parametrize(
        "postamble",
        [
            "Let me know if you want more detail.",
            "I hope this helps!",
            "Feel free to ask follow-up questions.",
            "Would you like me to expand on any point?",
        ],
    )
    def test_known_postambles_are_dropped(self, postamble):
        body = "The team migrated the store to WAL mode."
        assert clean_llm_summary(f"{body}\n\n{postamble}") == body

    def test_preamble_sharing_a_paragraph_with_content_is_split(self):
        text = (
            "Here is a summary: Varun released 4.0.7 to PyPI and npm, then "
            "rebuilt the code graph against the fixed archive filter."
        )
        out = clean_llm_summary(text)
        assert out.startswith("Varun released 4.0.7")

    def test_whole_answer_wrapped_in_a_code_fence_is_unwrapped(self):
        out = clean_llm_summary("```\nThe migration completed cleanly.\n```")
        assert out == "The migration completed cleanly."


class TestConservative:
    """False positives delete a user's real summary. These must never trip."""

    @pytest.mark.parametrize(
        "text",
        [
            # "Sure"/"Certainly" used as ordinary English mid-paragraph.
            "Sure enough, the migration completed. Certainly a milestone.",
            # A plain summary with no scaffolding at all.
            "Varun shipped 4.0.7 and fixed the code bridge filter.",
            # Content that merely mentions apologising.
            "The changelog apologises for the 4.0.6 version-pin mistake.",
            # A colon-bearing sentence that is genuine content, not a preamble.
            "Three things changed: the filter, the loader, and the tests.",
        ],
    )
    def test_legitimate_content_is_returned_unchanged(self, text):
        assert clean_llm_summary(text) == text

    def test_never_empties_a_non_empty_input(self):
        # Pure scaffolding with nothing behind it: a scaffolded summary still
        # beats a blank panel, so the original survives.
        assert clean_llm_summary("I apologize.") == "I apologize."
        assert clean_llm_summary("Sure!") == "Sure!"

    def test_empty_input_stays_empty(self):
        assert clean_llm_summary("") == ""
        assert clean_llm_summary(None) == ""  # type: ignore[arg-type]


class TestSystemPromptIsWired:
    """Mode B previously sent no system prompt — that is the root cause."""

    def test_prompt_forbids_the_scaffolding_we_strip(self):
        p = SUMMARY_SYSTEM_PROMPT.lower()
        assert "no preamble" in p
        assert "apolog" in p
        assert "sign-off" in p

    @pytest.mark.parametrize(
        "module_name",
        ["daily_reflection", "project_work_log", "session_summary"],
    )
    def test_every_generator_sends_it_and_cleans_output(self, module_name):
        import importlib
        import inspect

        mod = importlib.import_module(f"superlocalmemory.summaries.{module_name}")

        # Bound, not merely importable — a NameError in these helpers only fires
        # when a summary is actually generated, i.e. in front of the user.
        assert callable(mod.clean_llm_summary)
        assert isinstance(mod.SUMMARY_SYSTEM_PROMPT, str)

        ollama = inspect.getsource(mod._call_ollama)
        assert "SUMMARY_SYSTEM_PROMPT" in ollama, "Mode B sends no system prompt"
        assert "clean_llm_summary" in ollama, "Mode B output not sanitised"

        cloud = inspect.getsource(mod._call_cloud_llm)
        assert "SUMMARY_SYSTEM_PROMPT" in cloud
        assert "clean_llm_summary" in cloud, "Mode C output not sanitised"
