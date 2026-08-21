# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Show the owner what their memory contains, and do not flatter it.

The Summaries tab has never been able to answer "what does my memory know
ABOUT things". Its Today/Yesterday buttons are a date filter over atomic_facts
and its project dropdown reads tool_events -- there is no cluster linkage
anywhere in it. Two abstraction endpoints existed and were called by no
JavaScript at all.

So the Knowledge Overview card is new surface, and the risk with new surface
over this particular data is dressing it up. What is actually stored includes a
model's refusals, near-identical prose repeated hundreds of times, and counts
that mean something other than they appear to. Every test here pins a place
where the honest presentation and the flattering one differ.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest

from superlocalmemory.storage.schema import create_all_tables

_PROFILE = "default"

_REAL = (
    "The release shipped on a Tuesday after the harness caught two vacuous "
    "tests, and the owner signed it off the same afternoon."
)
_REFUSAL = (
    "Unfortunately, there is no information available about 'Gateway', "
    "'State', 'Bounded', or 'Claude' in the provided text."
)
_SCAFFOLDED = (
    "Here is a concise summary paragraph incorporating all 10 facts:\n\n"
    "The queue drained overnight and the backlog cleared without incident, "
    "which the owner recorded the following morning."
)


@pytest.fixture()
def store(tmp_path: pathlib.Path, monkeypatch) -> pathlib.Path:
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db))
    create_all_tables(conn)
    rows = [
        ("s-real", _REAL, 10, "2026-03-01T00:00:00Z", "2026-03-09T00:00:00Z"),
        ("s-refusal", _REFUSAL, 10, None, None),
        ("s-scaffolded", _SCAFFOLDED, 4, "2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z"),
        # Near-identical to s-real: a different tail, same opening. The UNIQUE
        # constraint is on exact content so both rows exist.
        ("s-dupe", _REAL + " Also the docs were updated.", 9,
         "2026-03-01T00:00:00Z", "2026-03-10T00:00:00Z"),
        # Covers no real memories: a summary of summaries. source_count 0.
        ("s-meta", "The projects have made significant progress in several areas "
                   "including documentation and compliance work.", 0, None, None),
    ]
    for sid, content, count, first, last in rows:
        conn.execute(
            "INSERT INTO consolidated_summaries "
            "(summary_id, profile_id, entity_id, entity_name, content, "
            " source_fact_ids, source_count, char_count, generated_by, "
            " source_earliest, source_latest) "
            "VALUES (?,?,?,?,?,'[]',?,?,'migrated',?,?)",
            (sid, _PROFILE, sid, "", content, count, len(content), first, last),
        )
    conn.commit()
    conn.close()

    import superlocalmemory.server.routes.abstraction as mod
    import superlocalmemory.server.routes.helpers as helpers

    monkeypatch.setattr(helpers, "DB_PATH", db, raising=False)
    monkeypatch.setattr(mod, "DB_PATH", db, raising=False)
    monkeypatch.setattr(mod, "get_active_profile", lambda: _PROFILE)
    return db


def _get(*, limit: int = 20, include_unusable: bool = False) -> dict:
    """Call the handler with every argument given explicitly.

    Calling a FastAPI handler as a plain function does NOT get its declared
    defaults: an omitted ``include_unusable`` arrives as the ``Query(False)``
    object, which is TRUTHY, so the first version of this helper silently asked
    for the unusable rows on every call and two assertions failed for a reason
    that had nothing to do with the code under test. FastAPI resolves those
    defaults itself over HTTP; a direct caller has to supply them.
    """
    import superlocalmemory.server.routes.abstraction as mod

    return json.loads(
        mod.get_consolidated(
            profile=_PROFILE, limit=limit, include_unusable=include_unusable,
        ).body
    )


class TestOnlyTheDisplayTableIsRead:
    def test_the_endpoint_names_no_corpus_table(self) -> None:
        """These summaries were IN atomic_facts until 4.0.10.

        Moving them out is only worth something if exactly one surface shows
        them and that surface reads only the display table. A join back to
        atomic_facts here would put the boundary back where it was.
        """
        import inspect

        import superlocalmemory.server.routes.abstraction as mod

        src = inspect.getsource(mod.get_consolidated)
        assert "consolidated_summaries" in src
        # Check SQL, not prose. The first version of this assertion looked for
        # the bare word and failed on the handler's own comment explaining why
        # a join back to the corpus would be wrong.
        import re

        corpus_sql = re.findall(
            r"(?:FROM|JOIN|UPDATE|INTO)\s+atomic_facts\b", src, re.IGNORECASE,
        )
        assert corpus_sql == [], (
            f"the summaries endpoint queries the retrieval corpus: {corpus_sql}"
        )


class TestJunkIsCountedNotRendered:
    def test_a_refusal_does_not_appear_as_a_summary(self, store) -> None:
        payload = _get(limit=20)
        shown = [s["content"] for s in payload["summaries"]]
        assert not any("no information available" in c for c in shown), (
            "a model's refusal was rendered as though it summarised something"
        )
        assert payload["unusable"] >= 1, (
            "the refusal was dropped without being counted, so the reader is "
            "shown a shorter list and told nothing"
        )

    def test_the_refusal_is_still_retrievable_for_inspection(self, store) -> None:
        """Counted, not deleted. A reader who wants to see them can."""
        payload = _get(limit=20, include_unusable=True)
        shown = [s["content"] for s in payload["summaries"]]
        assert any("no information available" in c for c in shown)
        assert all("quality" in s for s in payload["summaries"])

    def test_scaffolding_is_stripped_rather_than_the_row_dropped(
        self, store,
    ) -> None:
        """Clean, then judge -- the order the write path uses.

        Rows migrated from the old corpus were never cleaned. Judging first let
        "Here is a concise summary paragraph incorporating all 10 facts:" through
        as usable, because it does contain a summary, and put it at the top of
        the card.
        """
        payload = _get(limit=20)
        matches = [
            s for s in payload["summaries"] if "queue drained" in s["content"]
        ]
        assert matches, "a salvageable summary was dropped instead of cleaned"
        assert "concise summary paragraph" not in matches[0]["content"].lower(), (
            f"scaffolding survived to the screen: {matches[0]['content'][:120]!r}"
        )


class TestNearDuplicatesAreCollapsedAndCounted:
    def test_two_summaries_with_one_opening_become_one_card(self, store) -> None:
        payload = _get(limit=20)
        openings = [s["content"][:60] for s in payload["summaries"]]
        assert len(openings) == len(set(openings)), (
            f"the card would show near-identical rows: {openings}"
        )
        assert payload["near_duplicates"] >= 1

    def test_the_survivor_is_the_one_covering_more_memories(self, store) -> None:
        """Deterministic, and the more useful of the two.

        s-dupe covers 9 memories, s-real covers 10. Whichever is kept must be
        the same on every run, or the card reshuffles itself on refresh.
        """
        first = _get(limit=20)["summaries"]
        second = _get(limit=20)["summaries"]
        assert [s["summary_id"] for s in first] == [s["summary_id"] for s in second]
        kept = [s for s in first if "vacuous tests" in s["content"]]
        assert len(kept) == 1
        assert kept[0]["summary_id"] == "s-real"


class TestCountsMeanWhatTheySay:
    def test_a_summary_covering_no_memories_ranks_last(self, store) -> None:
        """353 stored summaries are summaries of summaries.

        Their honest source_count is 0, and a digest of the summarizer's own
        output is worth less to a reader than a digest of their own words.
        """
        ids = [s["summary_id"] for s in _get(limit=20)["summaries"]]
        assert "s-meta" in ids, "it must still be shown, just not first"
        assert ids.index("s-meta") == len(ids) - 1, (
            f"a summary covering no real memories is not last: {ids}"
        )

    def test_the_window_is_bounded(self) -> None:
        """This runs on a request thread over a table that can hold thousands."""
        import superlocalmemory.server.routes.abstraction as mod

        assert mod._SCAN_CEILING <= 1000

    def test_no_span_is_reported_when_there_is_nothing_to_span(
        self, store,
    ) -> None:
        """A false span is worse than none.

        The first draft spanned every source including other summaries, and
        produced "2026-08-18 to 2026-08-18" on a row whose ten sources were
        summaries written seconds apart -- the span of a summarisation run,
        presented as the stretch of work it covers.
        """
        by_id = {s["summary_id"]: s for s in _get(limit=20, include_unusable=True)["summaries"]}
        assert by_id["s-meta"]["source_earliest"] is None
        assert by_id["s-real"]["source_earliest"] == "2026-03-01T00:00:00Z"


class TestTheHealthLineIsShownToo:
    def test_it_reports_the_same_numbers_the_cli_does(self, store) -> None:
        """One measurement, two surfaces, so they cannot contradict each other."""
        import superlocalmemory.server.routes.abstraction as mod
        from superlocalmemory.core.memory_health import measure

        payload = json.loads(mod.get_memory_health().body)
        direct = measure(store)
        assert payload["live_facts"] == direct.live_facts
        assert payload["withheld_summaries"] == direct.withheld_summaries
        assert payload["summary"], "no plain-language lines for a reader"


class TestTheCardIsWiredIntoThePage:
    """A route nothing calls is the state this replaced.

    /api/v3/abstraction/persona and /communities have both been live and
    called by no JavaScript since Wave Q3.
    """

    @staticmethod
    def _ui_root() -> pathlib.Path:
        # superlocalmemory.ui carries no __init__.py, so ui.__file__ is None and
        # pathlib raises on it. Resolve from the package that does have one.
        import superlocalmemory

        return pathlib.Path(superlocalmemory.__file__).parent / "ui"

    @classmethod
    def _js(cls) -> str:
        return (cls._ui_root() / "js" / "od-memories.js").read_text(
            encoding="utf-8",
        )

    def test_the_summaries_tab_loads_it(self) -> None:
        js = self._js()
        assert "_loadKnowledgeOverview(id)" in js
        assert "if (tab === 'summary')" in js
        assert "/api/v3/abstraction/consolidated" in js
        assert "/api/v3/abstraction/health" in js

    def test_every_rendered_field_is_escaped(self) -> None:
        """Anything in a memory can reach this screen.

        The summarizer merges fact content verbatim, so a memory containing
        markup would render as markup. 68 facts on the author's store carry raw
        tool-call XML.
        """
        js = self._js()
        for fragment in (
            "_esc(s.entity_name)",
            "_esc(shown)",
            "_esc(text)",
            "_esc(l)",
        ):
            assert fragment in js, f"unescaped render path: {fragment}"

    def test_markdown_is_flattened_for_display(self) -> None:
        """Mode B/C summaries come back with "**Audit Round**" in them.

        Rendered as text those asterisks read as broken output to exactly the
        reader this card is for.
        """
        js = self._js()
        assert "_koverPlain(s.content)" in js
        assert "function _koverPlain" in js

    def test_the_asset_version_is_no_longer_hand_maintained(self) -> None:
        """This used to assert the literal matched the file's hash.

        That test was right when it was written and became the problem it was
        guarding against: it obliged a human to keep 64 numbers in step by hand,
        which is precisely the burden ``server/asset_versions.py`` removes by
        deriving them at serve time. Enforcing the literal would now mean a UI
        change fails CI for not updating a string nothing reads.

        What is worth asserting is that the derivation is in place, which
        tests/test_server/test_asset_versions_track_file_content.py covers
        directly. This is left as a pointer so the next person does not
        reintroduce the manual check.
        """
        from superlocalmemory.server.asset_versions import render_index

        root = self._ui_root()
        html = render_index(root / "index.html", root)
        assert 'od-memories.js?v=' in html
        # and it is the file's hash, not whatever the HTML happened to say
        import hashlib

        digest = hashlib.sha256(
            (root / "js" / "od-memories.js").read_bytes()
        ).hexdigest()[:8]
        assert f"od-memories.js?v={digest}" in html
