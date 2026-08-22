"""A schema version is a marker, not an event log.

The version table had no constraint saying a version appears once, and the six
call sites that stamp it all believed they were idempotent. One store carried
234,348 rows describing seven distinct versions. Collapsing that means deciding
which row is *the* row, and the answer is the earliest stamp, because that is
the one that records when the version actually landed.

This file exists because the migration that does the collapsing had no test,
while being able to delete a quarter of a million rows.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def version_table(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "store.db"))
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL, description TEXT NOT NULL DEFAULT '')"
    )
    conn.commit()
    yield conn
    conn.close()


def _fill(conn, rows):
    conn.executemany("INSERT INTO schema_version VALUES (?,?,?)", rows)
    conn.commit()


def _collapse(conn):
    from superlocalmemory.storage.migrations.M049_a_schema_version_marker_is_one_row import (
        apply,
    )

    apply(conn)
    return {
        row[0]: row for row in conn.execute(
            "SELECT version, applied_at, description FROM schema_version"
        )
    }


def test_the_earliest_stamp_is_the_one_that_survives(version_table):
    """Written out of order on purpose: the later stamp has the lower row id,
    so anything that picks by row id picks the wrong one."""
    _fill(version_table, [
        (7, "2026-08-01T00:00:00Z", "a later stamp"),
        (7, "2026-01-01T00:00:00Z", "when it really landed"),
        (7, "2026-06-01T00:00:00Z", "a middle one"),
    ])

    kept = _collapse(version_table)
    assert len(kept) == 1
    assert kept[7][1] == "2026-01-01T00:00:00Z"
    assert kept[7][2] == "when it really landed"


def test_a_description_only_a_duplicate_carried_is_recovered(version_table):
    """The surviving row may be the blank one. Recovery has to happen before
    the delete, because afterwards there is nothing left to read it from."""
    _fill(version_table, [
        (8, "2026-02-02T00:00:00Z", ""),
        (8, "2026-03-03T00:00:00Z", "what version eight did"),
    ])

    kept = _collapse(version_table)
    assert kept[8][1] == "2026-02-02T00:00:00Z", "the earliest stamp still wins"
    assert kept[8][2] == "what version eight did"


def test_every_version_keeps_exactly_one_row(version_table):
    _fill(version_table, [
        (version, f"2026-0{1 + copy}-01T00:00:00Z", f"v{version}")
        for version in (3, 4, 5)
        for copy in range(4)
    ])
    assert version_table.execute(
        "SELECT COUNT(*) FROM schema_version"
    ).fetchone()[0] == 12

    kept = _collapse(version_table)
    assert sorted(kept) == [3, 4, 5]
    for version in (3, 4, 5):
        assert kept[version][1] == "2026-01-01T00:00:00Z"


def test_stamping_the_same_version_twice_changes_nothing(version_table):
    """Once the constraint exists, the call sites that believed they were
    idempotent have to actually be."""
    from superlocalmemory.storage.migrations import set_schema_version

    _fill(version_table, [(9, "2026-01-01T00:00:00Z", "nine landed")])
    _collapse(version_table)

    set_schema_version(version_table, 9)
    version_table.commit()

    rows = version_table.execute(
        "SELECT applied_at, description FROM schema_version WHERE version = 9"
    ).fetchall()
    assert rows == [("2026-01-01T00:00:00Z", "nine landed")]


def test_stamping_a_new_version_still_records_it(version_table):
    """The control. Ignoring a repeat must not ignore a first."""
    from superlocalmemory.storage.migrations import set_schema_version

    _fill(version_table, [(9, "2026-01-01T00:00:00Z", "nine landed")])
    _collapse(version_table)

    set_schema_version(version_table, 10)
    version_table.commit()
    assert sorted(
        row[0] for row in version_table.execute("SELECT version FROM schema_version")
    ) == [9, 10]


def test_running_it_twice_is_a_no_op(version_table):
    _fill(version_table, [
        (11, "2026-01-01T00:00:00Z", "eleven"),
        (11, "2026-05-01T00:00:00Z", "eleven again"),
    ])
    first = _collapse(version_table)
    second = _collapse(version_table)
    assert first == second


def test_a_store_with_no_such_table_is_left_alone(tmp_path):
    from superlocalmemory.storage.migrations.M049_a_schema_version_marker_is_one_row import (
        apply,
    )

    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    apply(conn)  # must not raise
    conn.close()


class TestItFinishesOnTheStoreItWasWrittenFor:
    """Correct and unfinishable is not correct.

    The store this migration exists for had 234,348 rows over seven versions.
    A first draft picked the surviving row with a subquery correlated on
    version, which re-scans the whole table once per row: about 7.8 billion row
    comparisons. Measured, it ran over twenty-five minutes at full CPU without
    completing — which, on a first upgrade with no output, is indistinguishable
    from a hang. Ranking each version once takes 324 ms on the same table.
    """

    def test_it_does_not_rescan_the_table_once_per_row(self, version_table):
        """Read from the query planner, so the shape is pinned rather than a
        timing that varies by machine."""
        import inspect

        from superlocalmemory.storage.migrations import (
            M049_a_schema_version_marker_is_one_row as migration,
        )

        ranked = (
            "SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER ("
            "PARTITION BY version ORDER BY applied_at ASC, rowid ASC) AS rn "
            "FROM schema_version) WHERE rn = 1"
        )
        correlated = (
            "SELECT a.rowid FROM schema_version AS a WHERE a.rowid = ("
            "SELECT b.rowid FROM schema_version AS b WHERE b.version = a.version "
            "ORDER BY b.rowid LIMIT 1)"
        )

        def plan(sql: str) -> str:
            return " ".join(
                str(row[3]) for row in
                version_table.execute("EXPLAIN QUERY PLAN " + sql)
            ).upper()

        # The control: the shape being avoided really is the one the planner
        # calls correlated. Without this the assertion below proves nothing.
        assert "CORRELATED" in plan(correlated)
        assert "CORRELATED" not in plan(ranked)

        source = inspect.getsource(migration)
        assert "ROW_NUMBER() OVER (" in source
        assert " AS outer_row " not in source and " AS inner_row " not in source, (
            "the table is aliased against itself again, which is the shape "
            "that re-scans it once per row"
        )

    def test_it_finishes_on_a_table_the_size_of_a_real_one(self, version_table):
        """Fifty thousand rows over seven versions. The correlated form does
        about 357 million comparisons here and takes minutes; this must be
        comfortably under a second on any machine that can run the suite."""
        import time

        rows = [
            (version, f"2026-0{1 + (index % 9)}-01T00:00:00Z", "")
            for version in range(7)
            for index in range(7_000)
        ]
        version_table.executemany("INSERT INTO schema_version VALUES (?,?,?)", rows)
        version_table.commit()
        assert version_table.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()[0] == 49_000

        from superlocalmemory.storage.migrations.M049_a_schema_version_marker_is_one_row import (
            apply,
        )

        started = time.perf_counter()
        apply(version_table)
        elapsed = time.perf_counter() - started

        assert version_table.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()[0] == 7
        assert elapsed < 10.0, (
            f"collapsing 49,000 rows took {elapsed:.1f} s; on the 234,348-row "
            f"store this migration exists for that is minutes of what looks "
            f"like a hang"
        )
