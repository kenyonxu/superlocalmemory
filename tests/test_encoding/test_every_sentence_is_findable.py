# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Every sentence of a saved memory is findable on its own.

On the author's store (13 Oct 2026) a 767-character launch note was saved as
the whole note plus four facts: its first two bullets. "Product Hunt page fix
needed: license MIT to AGPL-3.0." lived only inside the whole-note fact, whose
single embedding is dominated by the note's opening, and recall abstained on
"Product Hunt license fix". Three hard-coded caps of five derived facts per
memory (11 Oct) overrode the configured ten; before them, 3,371 of 4,260 long
memories already had six facts or fewer.
"""

from __future__ import annotations

from superlocalmemory.core.config import EncodingConfig
from superlocalmemory.encoding.coverage_facts import coverage_facts, source_segments
from superlocalmemory.encoding.fact_extractor import FactExtractor
from superlocalmemory.storage.models import AtomicFact, MemoryRecord, Mode

NOTE = (
    "SLM relaunch schedule (decided by Varun, Tuesday 13 Oct 2026).\n"
    "- Tonight after 10:30 pm IST: Claude fact-checks blog.md and x-article.md from "
    "slm-launch-42/codex-package. The blog then goes live at qualixar.com/research/blog "
    "(canonical), followed by Dev.to (canonical_url) and Medium (Import a story). Product "
    "Hunt is scheduled for Wednesday. The full YouTube demo build resumes from "
    "slm-launch-42/demo/RESUME-STATE.md.\n"
    "- Wednesday 14 Oct: X Article at 9:30 AM. Teaser with posts on X, YouTube, LinkedIn, "
    "Mastodon and Bluesky at 10:30 AM. Product Hunt goes live at 12:31 PM IST. All remaining "
    "distribution happens the same day; nothing is left for Thursday.\n"
    "- Product Hunt page fix needed: license MIT to AGPL-3.0.\n"
    "- Plan file: slm-launch-42/RELAUNCH-PUBLISH-PLAN.md."
)
LICENSE = "Product Hunt page fix needed: license MIT to AGPL-3.0."

#: The four facts the 4.2.4 extractor kept for NOTE (memory 01f68f08).
KEPT = [
    "The blog then goes live at qualixar.com/research/blog (canonical), followed by "
    "Dev.to (canonical_url) and Medium (Import a story).",
    "Tonight after 10:30 pm IST: Claude fact-checks blog.md and x-article.md from "
    "slm-launch-42/codex-package.",
    "SLM relaunch schedule (decided by Varun, Tuesday 13 Oct 2026).",
    "Product Hunt is scheduled for Wednesday.",
]


def _facts(texts):
    return [AtomicFact(content=t) for t in texts]


def test_bullets_are_their_own_segments():
    segments = source_segments(NOTE)
    assert LICENSE in segments
    assert "Plan file: slm-launch-42/RELAUNCH-PUBLISH-PLAN.md." in segments
    assert not any(s.startswith("- ") for s in segments)


def test_the_license_line_becomes_its_own_fact():
    added = [f.content for f in coverage_facts(NOTE, _facts(KEPT), session_date="2026-10-13")]
    assert LICENSE in added
    assert "Wednesday 14 Oct: X Article at 9:30 AM." in added
    # What the extractor already holds is not stored twice.
    assert not set(KEPT) & set(added)


def test_coverage_facts_are_verbatim_source_text():
    for fact in coverage_facts(NOTE, [], session_date="2026-10-13"):
        assert fact.content in NOTE
        assert fact.observation_date == "2026-10-13"


def test_a_sentence_reworded_by_the_extractor_counts_as_covered():
    reworded = _facts(["The Product Hunt page needs its license fixed from MIT to AGPL-3.0"])
    added = [f.content for f in coverage_facts(NOTE, reworded)]
    assert LICENSE not in added


def test_a_one_sentence_memory_adds_nothing():
    assert coverage_facts("I prefer dark mode in every editor I use.", []) == []


def test_filler_and_fragments_are_skipped():
    text = "Thanks for the help today.\nOk.\nThe deploy key rotates every 90 days on Fridays."
    added = [f.content for f in coverage_facts(text, [])]
    assert added == ["The deploy key rotates every 90 days on Fridays."]


def test_the_ceiling_holds_and_keeps_source_order():
    text = "\n".join(f"- Service number {i} listens on port {8000 + i} today." for i in range(60))
    added = [f.content for f in coverage_facts(text, [], limit=40)]
    assert len(added) == 40
    assert added[0].startswith("Service number 0 ") and added[-1].startswith("Service number 39 ")


def test_a_named_speaker_stays_attached():
    text = "[Alex]: I moved payroll to Rust last month. [Priya]: I kept billing on Go for now."
    added = [f.content for f in coverage_facts(text, [])]
    assert "[Alex]: I moved payroll to Rust last month." in added
    assert "[Priya]: I kept billing on Go for now." in added


def test_the_extractor_honours_its_configured_cap():
    text = " ".join(
        f"Project Atlas milestone {i} shipped on 2026-09-{10 + i:02d} with Kafka."
        for i in range(12)
    )
    facts = FactExtractor(EncodingConfig(max_facts_per_chunk=10), mode=Mode.A).extract_facts(
        [text], "s1", "2026-10-13"
    )
    assert len(facts) == 10


def test_source_fact_set_keeps_configured_derivatives_and_adds_coverage(tmp_path):
    from superlocalmemory.core.source_fact_set import source_fact_set
    from tests.test_core.test_source_fidelity_guard import _db

    db = _db(tmp_path / "memory.db")
    record = MemoryRecord(content=NOTE, session_date="2026-10-13")
    db.store_memory(record)
    live, protected = source_fact_set(_facts(KEPT), NOTE, record, [], db=db)
    contents = [f.content for f in live]
    assert LICENSE in contents
    assert NOTE in contents  # the whole note is still the protected source
    assert len(protected) == 1
    assert len(contents) == len(set(contents))


def test_source_fact_set_no_longer_drops_the_sixth_derivative(tmp_path):
    from superlocalmemory.core.source_fact_set import source_fact_set
    from tests.test_core.test_source_fidelity_guard import _db

    db = _db(tmp_path / "memory.db")
    lines = [f"Region {c} runs the billing service on Postgres 16." for c in "ABCDEFG"]
    text = " ".join(lines)
    record = MemoryRecord(content=text)
    db.store_memory(record)
    live, _ = source_fact_set(_facts(lines), text, record, [], db=db)
    assert set(lines) <= {f.content for f in live}


def test_markup_lines_are_not_facts():
    # Seen in the author's store: a task notification captured by a hook.
    text = ("<task-notification>\n<summary>Agent \"WP-08 portable kit\" completed</summary>\n"
            "The portable kit now installs on Linux ARM64 without a compiler.")
    added = [f.content for f in coverage_facts(text, [])]
    assert added == ["The portable kit now installs on Linux ARM64 without a compiler."]
