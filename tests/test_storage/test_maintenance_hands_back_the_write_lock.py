"""Tidying up must not make saving a memory wait.

Both housekeeping passes run against the same store people are using. Holding
the single write lock for the length of a pass means a memory being saved waits
for the whole pass — measured at 1,291 ms while removing 123,888 rows from a
1 GB store, against a budget of 1,500 ms for the whole save.

So each pass takes and releases the lock per piece. The batching inside a pass
cannot do that on its own: holding the connection is what holds the lock, so a
caller that opens one connection for the whole pass holds it for the whole
pass however carefully the pass batches.
"""

from __future__ import annotations

import contextlib
import pathlib
import sqlite3
import time

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.migration_runner import apply_all

    config = SLMConfig.load()
    db = DatabaseManager(config.db_path)
    db.initialize(schema)
    apply_all(pathlib.Path(state_path("learning.db")), pathlib.Path(config.db_path))
    return db


def _fill_expired(db, rows):
    """Rows old enough that the time rule removes every one of them."""
    with db.transaction():
        for index in range(rows):
            db.execute(
                "INSERT INTO consolidation_log (action_id, profile_id, "
                "action_type, new_fact_id, existing_fact_id, reason, timestamp) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"a{index}", "default", "add", f"n{index}", f"e{index}",
                 "old enough for the time rule", "2020-01-01T00:00:00Z"),
            )


def _counting_opener(db):
    """Wraps the manager's connection factory and counts each acquisition."""
    holds: list[float] = []
    real = db.raw_connection

    @contextlib.contextmanager
    def opener():
        started = time.perf_counter()
        with real() as conn:
            yield conn
        holds.append(time.perf_counter() - started)

    return opener, holds


def test_the_sweep_takes_the_lock_many_times_rather_than_once(store):
    from superlocalmemory.storage.retention_policy import (
        BOUNDED_BATCH,
        run_retention_bounded,
    )

    rows = BOUNDED_BATCH * 3 + 17
    _fill_expired(store, rows)

    opener, holds = _counting_opener(store)
    removed = run_retention_bounded(opener, batch_size=BOUNDED_BATCH)

    assert removed.get("consolidation_log") == rows
    assert store.execute(
        "SELECT COUNT(*) AS c FROM consolidation_log"
    )[0]["c"] == 0
    # Four pieces for this table alone, plus one probe per other policy.
    assert len(holds) > 4, (
        f"the sweep took the lock {len(holds)} times for {rows} rows across "
        f"every policy; it is not handing the lock back"
    )


def test_a_bounded_piece_removes_no_more_than_it_was_asked_to(store):
    from superlocalmemory.storage.retention_policy import (
        REGISTERED_POLICIES,
        apply_policy,
    )

    _fill_expired(store, 250)
    with store.raw_connection() as conn:
        removed = apply_policy(
            conn, REGISTERED_POLICIES["consolidation_log"], limit=100,
        )
    assert removed == 100
    assert store.execute(
        "SELECT COUNT(*) AS c FROM consolidation_log"
    )[0]["c"] == 150


def test_the_bounded_sweep_removes_exactly_what_the_unbounded_one_would(
    tmp_path, monkeypatch,
):
    """The whole point is that this is the same sweep, in pieces."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.migration_runner import apply_all
    from superlocalmemory.storage.retention_policy import (
        run_retention,
        run_retention_bounded,
    )

    results = []
    for index, bounded in enumerate((False, True)):
        root = tmp_path / f"store{index}"
        root.mkdir()
        monkeypatch.setenv("SLM_DATA_DIR", str(root))
        config = SLMConfig.load()
        db = DatabaseManager(config.db_path)
        db.initialize(schema)
        apply_all(pathlib.Path(state_path("learning.db")), pathlib.Path(config.db_path))
        _fill_expired(db, 500)

        if bounded:
            results.append(run_retention_bounded(db.raw_connection, batch_size=64))
        else:
            with db.raw_connection() as conn:
                results.append(run_retention(conn))
        db.close()

    assert results[0] == results[1]
    assert results[0].get("consolidation_log") == 500


def test_re_reading_what_is_filed_as_a_plan_also_hands_the_lock_back(store):
    from superlocalmemory.storage.migrations import (
        M048_upcoming_holds_only_what_is_upcoming as reclassify,
    )

    opener, holds = _counting_opener(store)
    reclassify.apply(open_connection=opener)
    assert holds, "the pass never took the lock at all"


def test_that_pass_still_accepts_a_connection_the_caller_owns(store):
    """The migration runner owns its connection and nothing else is writing."""
    from superlocalmemory.storage.migrations import (
        M048_upcoming_holds_only_what_is_upcoming as reclassify,
    )

    with store.raw_connection() as conn:
        reclassify.apply(conn)  # must not raise

    with pytest.raises(ValueError):
        reclassify.apply()
    with pytest.raises(ValueError):
        reclassify.apply(conn, open_connection=store.raw_connection)
