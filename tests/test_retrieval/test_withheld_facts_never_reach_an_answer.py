# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Marking a row withheld is not the same as it being unreachable.

The review note on this work was explicit: quarantining in the schema without
putting the filter on every retrieval path leaves quarantined facts still
appearing, and the answer is to isolate it to ONE place rather than sprinkle
``AND quarantined = 0`` across the channels.

Three candidate choke points were checked before one was chosen, and the two
rejected ones are the interesting part, because each looked right:

  ``_scope_where`` is the canonical scope predicate and the obvious home. It is
  also spliced against ``graph_edges``, ``temporal_events``, ``memories``,
  ``bm25_tokens``, ``fact_temporal_validity`` and ``correction_cases`` as well
  as ``atomic_facts`` -- 28 call sites, eight of them on tables with no such
  column. A reference there does not filter, it raises.

  ``ForgettingFilter`` already excludes zones in exactly one place, so it looks
  purpose-built. But ``register_forgetting_filter`` no-ops when forgetting is
  disabled, and ``_DEEP_EXCLUDED_ZONES`` is empty, so it withholds nothing in
  deep recall and nothing at all for a user who turned forgetting off.

  ``DatabaseManager.get_facts_by_ids`` is where it went. Every channel
  re-authorises candidates through it, the engine hydrates the fused set from
  it, and ``engine.py`` drops anything it does not return.

These tests pin the choke point, the escape hatch repair needs, and the
property that actually matters: no path returns a withheld row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.schema import create_all_tables

_PROFILE = "default"


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db))
    create_all_tables(conn)
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', ?, 'source')", (_PROFILE,),
    )
    for fid, quarantined, text in (
        ("keep-1", 0, "Varun ships 4.0.10 with a repair that runs on upgrade."),
        ("keep-2", 0, "The release criteria include a full suite and two audits."),
        ("hide-1", 1, "Unfortunately, there is no information available."),
        ("hide-2", 1, "The Pro projects have made significant progress."),
    ):
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
            " quarantined, created_at) VALUES (?, 'm1', ?, ?, ?, ?)",
            (fid, _PROFILE, text, quarantined, "2026-08-01T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()
    return db


ALL_IDS = ["keep-1", "keep-2", "hide-1", "hide-2"]
HIDDEN = {"hide-1", "hide-2"}
KEPT = {"keep-1", "keep-2"}


class TestTheChokePointHolds:
    def test_hydration_does_not_return_a_withheld_fact(self, store: Path) -> None:
        got = DatabaseManager(str(store)).get_facts_by_ids(ALL_IDS, _PROFILE)
        ids = {f.fact_id for f in got}
        assert ids == KEPT, f"withheld rows leaked: {sorted(ids & HIDDEN)}"

    def test_repair_paths_can_still_see_them(self, store: Path) -> None:
        """A row nothing can read is a row nothing can fix or erase.

        Export, erasure and the repair itself all need to reach a withheld row.
        The opt-in is keyword-only so every caller that takes it is findable in
        one search.
        """
        got = DatabaseManager(str(store)).get_facts_by_ids(
            ALL_IDS, _PROFILE, include_quarantined=True,
        )
        assert {f.fact_id for f in got} == set(ALL_IDS)

    def test_the_opt_in_is_keyword_only(self, store: Path) -> None:
        """A positional flag gets passed by accident. This one cannot be.

        get_facts_by_ids already takes two positional booleans
        (include_global, include_shared); a third would be one transposition
        away from turning the guard off at a call site that meant nothing by it.
        """
        import inspect

        sig = inspect.signature(DatabaseManager.get_facts_by_ids)
        param = sig.parameters["include_quarantined"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is False, "the safe value must be the default"

    def test_an_unmigrated_store_still_answers(self, tmp_path: Path) -> None:
        """No column must mean no filter, not an exception on every recall.

        A DatabaseManager can be pointed at a store engine init never touched.
        Filtering on a column that is not there would turn a cosmetic gap into
        total recall failure.
        """
        db = tmp_path / "old.db"
        conn = sqlite3.connect(str(db))
        create_all_tables(conn)
        conn.execute(
            "INSERT INTO memories (memory_id, profile_id, content) "
            "VALUES ('m1', ?, 's')", (_PROFILE,),
        )
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content,"
            " created_at) VALUES ('f1','m1',?,?,?)",
            (_PROFILE, "a memory from before the column existed",
             "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.execute("ALTER TABLE atomic_facts DROP COLUMN quarantined")
        conn.commit()
        conn.close()

        mgr = DatabaseManager(str(db))
        assert mgr._has_quarantine_column() is False
        assert [f.fact_id for f in mgr.get_facts_by_ids(["f1"], _PROFILE)] == ["f1"]


class TestTheChokePointIsTheOnlyOne:
    def test_the_engine_drops_a_candidate_it_cannot_hydrate(self) -> None:
        """This is WHY one filter at hydration covers every channel.

        bm25, temporal and entity build their own SQL against atomic_facts and
        never call get_facts_by_ids; semantic goes through the vector store. All
        of them still lose a candidate here, because a fused result with no
        hydrated fact is skipped when the response is assembled. If that
        `continue` ever became a fallback that emitted a partial result, this
        design would silently stop working.
        """
        import inspect

        from superlocalmemory.retrieval import engine as engine_mod

        src = inspect.getsource(engine_mod.RetrievalEngine)
        assert "fact = fact_map.get(fr.fact_id)\n            if fact is None:\n                continue" in src, (
            "the engine no longer drops candidates it cannot hydrate, so a "
            "withheld fact could reach the response through a channel that "
            "builds its own SQL"
        )

    def test_no_retrieval_module_names_the_display_table(self) -> None:
        """The 3.6 boundary, asserted rather than intended.

        A summary became an answer last time because a writer reached into the
        corpus. It would become one again if a reader reached into the display
        table. community_summaries is read only after retrieval finishes, by
        _community_context; consolidated_summaries is read only by the
        dashboard route.
        """
        import pathlib

        root = pathlib.Path(engine_dir())
        offenders: list[str] = []
        for path in sorted(root.glob("*.py")):
            if "consolidated_summaries" in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        assert offenders == [], (
            f"retrieval modules referencing the display table: {offenders}"
        )


def engine_dir() -> str:
    from superlocalmemory import retrieval

    return str(Path(retrieval.__file__).parent)


class TestTheSemanticIndexDoesNotSpendSlotsOnWithheldRows:
    def test_knn_excludes_withheld_facts(self, tmp_path: Path) -> None:
        """A withheld row must not take a nearest-neighbour slot.

        Its vector stays in the index -- quarantine is reversible and deleting
        the projection would cost a re-embed to undo -- so the filter is at
        query time. On the author's store this mattered a great deal: searching
        with a vector taken from one withheld summary returned 50 of 50
        neighbours withheld, because model-written summaries of similar
        clusters land close together. The semantic channel was contributing
        nothing at all for any query near them, while appearing to answer.
        """
        from superlocalmemory.retrieval.vector_store import (
            VectorStore,
            VectorStoreConfig,
        )

        db = tmp_path / "vec.db"
        conn = sqlite3.connect(str(db))
        create_all_tables(conn)
        conn.execute(
            "INSERT INTO memories (memory_id, profile_id, content) "
            "VALUES ('m1', ?, 's')", (_PROFILE,),
        )
        for fid, q in (("v-keep", 0), ("v-hide", 1)):
            conn.execute(
                "INSERT INTO atomic_facts (fact_id, memory_id, profile_id,"
                " content, quarantined, created_at) "
                "VALUES (?, 'm1', ?, ?, ?, '2026-08-01T00:00:00+00:00')",
                (fid, _PROFILE, f"content for {fid}", q),
            )
        conn.commit()
        conn.close()

        vs = VectorStore(db, VectorStoreConfig(dimension=4))
        if not vs.available:
            pytest.skip("sqlite-vec extension unavailable")
        assert vs.upsert("v-keep", _PROFILE, [1.0, 0.0, 0.0, 0.0])
        assert vs.upsert("v-hide", _PROFILE, [1.0, 0.0, 0.0, 0.0])
        assert vs._has_quarantine_column() is True

        hits = vs.search([1.0, 0.0, 0.0, 0.0], top_k=10, profile_id=_PROFILE)
        ids = {fid for fid, _ in hits}
        assert "v-hide" not in ids, "a withheld fact occupied a KNN slot"
        assert "v-keep" in ids, (
            "the filter removed a live fact too, so this test would also pass "
            "with search broken"
        )
