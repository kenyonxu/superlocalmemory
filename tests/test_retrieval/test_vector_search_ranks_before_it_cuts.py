# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""KNN must return the nearest top_k, not the first top_k the join emitted.

`VectorStore.search()` asks vec0 for k neighbours and joins them to
`embedding_metadata` to recover fact ids. A vec0 row whose metadata mate is gone
-- an orphan -- occupies a neighbour slot and is then discarded by the join, so
the query comes back short and the code retries with a larger k. That retry is
common: 29 of 60 queries take it on the 0.95 GB archive.

The result is that `rows` can be longer than top_k while the SQL carries no
ORDER BY. Trimming before ranking therefore trimmed by whatever order SQLite's
planner chose for the join, and ranked only the survivors -- so a nearer fact
could be dropped in favour of a farther one. On that archive the produced set
matched the true nearest-k in all 29 cases, because the join does emit in
distance order today. These tests exist because that is a property of the
current planner, not a promise: the failure is silent, and it returns the wrong
memory rather than an error.
"""

from __future__ import annotations

import pytest

from superlocalmemory.retrieval.vector_store import VectorStore, VectorStoreConfig


class _Row(dict):
    """sqlite3.Row is subscriptable by column name; a dict is enough."""


class _Conn:
    """Returns a fixed row list, deliberately NOT in distance order.

    That is the whole point: the production SQL does not order, so a test that
    feeds pre-sorted rows cannot tell the two implementations apart.
    """

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.queries: list[str] = []

    def execute(self, sql: str, params: tuple = ()):  # noqa: D102
        self.queries.append(sql)
        if "COUNT(*)" in sql:
            return _Cursor([_Row({"c": len(self._rows)})])
        return _Cursor(list(self._rows))


class _Cursor:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def fetchall(self) -> list[_Row]:
        return self._rows

    def fetchone(self) -> _Row | None:
        return self._rows[0] if self._rows else None


@pytest.fixture
def store(tmp_path, monkeypatch) -> VectorStore:
    """A VectorStore whose connection is ours, with the extension bypassed.

    Loading sqlite-vec and building a 12k-vector table would test SQLite, not
    the ranking decision under test.
    """
    monkeypatch.setattr(VectorStore, "_try_load_extension", lambda self: True)
    monkeypatch.setattr(VectorStore, "_ensure_vec0_table", lambda self: None)
    return VectorStore(tmp_path / "memory.db", VectorStoreConfig(dimension=8))


def _attach(store: VectorStore, rows: list[_Row], monkeypatch) -> _Conn:
    conn = _Conn(rows)

    from contextlib import contextmanager

    @contextmanager
    def _managed(self):
        yield conn

    monkeypatch.setattr(VectorStore, "_managed_connection", _managed)
    return conn


class TestTheNearestFactsWin:
    def test_the_farthest_rows_are_cut_not_the_last_ones_emitted(
        self, store: VectorStore, monkeypatch
    ) -> None:
        """Six candidates, top_k=3, emitted worst-first.

        Position-based trimming keeps the three FARTHEST facts here and then
        sorts them, so the caller is handed the worst three of six and never
        sees the nearest. That is a wrong answer, not a reordered one.
        """
        rows = [
            _Row({"rowid": 1, "distance": 0.90, "fact_id": "far-a"}),
            _Row({"rowid": 2, "distance": 0.80, "fact_id": "far-b"}),
            _Row({"rowid": 3, "distance": 0.70, "fact_id": "mid-c"}),
            _Row({"rowid": 4, "distance": 0.10, "fact_id": "near-d"}),
            _Row({"rowid": 5, "distance": 0.05, "fact_id": "near-e"}),
            _Row({"rowid": 6, "distance": 0.01, "fact_id": "near-f"}),
        ]
        _attach(store, rows, monkeypatch)

        got = store.search([0.1] * 8, top_k=3, profile_id="default")

        assert [fid for fid, _ in got] == ["near-f", "near-e", "near-d"], (
            f"search returned {[f for f, _ in got]}; the three nearest facts "
            "were dropped in favour of the three the join happened to emit first"
        )

    def test_ties_at_the_boundary_are_broken_by_fact_id(
        self, store: VectorStore, monkeypatch
    ) -> None:
        """Equal distance at the cut is where membership churns.

        Without a total order it is the planner that decides which of two
        equidistant facts enters the result, so the same question can return
        either one. The tie-break makes that choice a property of the data.
        """
        rows = [
            _Row({"rowid": 1, "distance": 0.20, "fact_id": "zzz"}),
            _Row({"rowid": 2, "distance": 0.20, "fact_id": "aaa"}),
            _Row({"rowid": 3, "distance": 0.10, "fact_id": "mmm"}),
        ]
        _attach(store, rows, monkeypatch)

        first = store.search([0.1] * 8, top_k=2, profile_id="default")
        _attach(store, list(reversed(rows)), monkeypatch)
        second = store.search([0.1] * 8, top_k=2, profile_id="default")

        assert [f for f, _ in first] == ["mmm", "aaa"]
        assert first == second, (
            "the same distances in a different emission order produced a "
            f"different result: {first} vs {second}"
        )

    def test_it_still_returns_no_more_than_asked_for(
        self, store: VectorStore, monkeypatch
    ) -> None:
        """Ranking before cutting must not stop the cut happening.

        Returning the full expanded candidate list would push extra candidates
        into fusion and silently widen every semantic recall.
        """
        rows = [
            _Row({"rowid": i, "distance": i / 100.0, "fact_id": f"f{i:02d}"})
            for i in range(1, 21)
        ]
        _attach(store, rows, monkeypatch)

        got = store.search([0.1] * 8, top_k=5, profile_id="default")

        assert len(got) == 5
        assert [f for f, _ in got] == ["f01", "f02", "f03", "f04", "f05"]

    def test_similarity_is_still_reported_not_distance(
        self, store: VectorStore, monkeypatch
    ) -> None:
        """The contract is (fact_id, similarity), and callers rank on it.

        Returning distance would invert the ranking everywhere downstream while
        every test above still passed, since they only assert on ids.
        """
        rows = [_Row({"rowid": 1, "distance": 0.25, "fact_id": "only"})]
        _attach(store, rows, monkeypatch)

        got = store.search([0.1] * 8, top_k=1, profile_id="default")

        assert got == [("only", pytest.approx(0.75))]
