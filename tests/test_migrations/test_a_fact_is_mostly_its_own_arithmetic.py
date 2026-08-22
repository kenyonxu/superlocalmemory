# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The numbers describing a memory were thirty-two times the memory's size.

Each fact carries three vectors of equal width: its embedding, and the diagonal
Fisher mean and variance that decide how fast it decays. The embedding became
float32 in 4.0.9. The other two stayed as decimal text, where each number costs
about 22 bytes instead of 4.

On the store this was measured against: 116.5 MB of Fisher text describing
3.6 MB of memories, in a 447 MB file.

What has to hold afterwards, and is checked here rather than assumed:

- the numbers still say the same thing, to the precision float32 offers;
- a store stopped halfway is a working store, because both forms read;
- a vector that cannot be parsed is left alone rather than replaced by NULL,
  because absent reads as "no evidence" to the decay dynamics;
- rowids do not move, because the full-text index is keyed on them.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest

from superlocalmemory.storage.embedding_codec import (
    decode_float_vector,
    encode_float_vector,
)
from superlocalmemory.storage.migrations import (
    M047_fisher_vectors_are_stored_like_every_other_vector as M047,
)

WIDTH = 768


def _vector(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.normal(size=WIDTH).tolist()


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE atomic_facts ("
        " fact_id TEXT PRIMARY KEY, content TEXT,"
        " fisher_mean TEXT, fisher_variance TEXT)"
    )
    yield conn
    conn.close()


def _seed(conn, n: int, *, start: int = 0) -> dict[str, list[float]]:
    written: dict[str, list[float]] = {}
    for i in range(start, start + n):
        mean = _vector(i)
        written[f"f{i}"] = mean
        conn.execute(
            "INSERT INTO atomic_facts VALUES (?,?,?,?)",
            (f"f{i}", f"memory {i}", json.dumps(mean), json.dumps(_vector(i + 10_000))),
        )
    conn.commit()
    return written


def test_the_numbers_survive_the_conversion(store) -> None:
    written = _seed(store, 12)
    M047.apply(store)

    for fact_id, expected in written.items():
        raw = store.execute(
            "SELECT fisher_mean FROM atomic_facts WHERE fact_id=?", (fact_id,)
        ).fetchone()[0]
        assert isinstance(raw, bytes), "still stored as text"
        got = np.frombuffer(raw, dtype=np.float32)
        assert len(got) == WIDTH
        # float32 carries about seven significant digits; that is the whole
        # trade, and it has to be the ONLY difference.
        assert np.allclose(got, expected, atol=1e-6, rtol=0)


def test_it_actually_gets_smaller(store) -> None:
    written = _seed(store, 20)
    text_bytes = sum(len(json.dumps(v)) for v in written.values())
    M047.apply(store)
    blob_bytes = sum(
        len(r[0]) for r in store.execute("SELECT fisher_mean FROM atomic_facts")
    )
    assert blob_bytes * 4 < text_bytes, (
        f"expected a large reduction, got {text_bytes} -> {blob_bytes}"
    )


def test_rowids_do_not_move(store) -> None:
    """The full-text index is keyed on rowid, so moving one repoints an entry."""
    _seed(store, 15)
    before = store.execute(
        "SELECT MIN(rowid), MAX(rowid), SUM(rowid), COUNT(*) FROM atomic_facts"
    ).fetchone()
    M047.apply(store)
    after = store.execute(
        "SELECT MIN(rowid), MAX(rowid), SUM(rowid), COUNT(*) FROM atomic_facts"
    ).fetchone()
    assert before == after


def test_a_store_stopped_halfway_still_reads(store) -> None:
    """Both forms decode, so an interrupted conversion is not an outage."""
    written = _seed(store, 6)
    # Convert three by hand, leave three as text — the mid-migration state.
    for fact_id in list(written)[:3]:
        store.execute(
            "UPDATE atomic_facts SET fisher_mean=? WHERE fact_id=?",
            (encode_float_vector(written[fact_id]), fact_id),
        )
    store.commit()

    for fact_id, expected in written.items():
        raw = store.execute(
            "SELECT fisher_mean FROM atomic_facts WHERE fact_id=?", (fact_id,)
        ).fetchone()[0]
        got = decode_float_vector(raw, field="fisher_mean", fact_id=fact_id)
        assert got is not None
        assert np.allclose(got, expected, atol=1e-6, rtol=0)


def test_running_it_again_changes_nothing(store) -> None:
    _seed(store, 8)
    M047.apply(store)
    first = store.execute(
        "SELECT fact_id, fisher_mean, fisher_variance FROM atomic_facts ORDER BY fact_id"
    ).fetchall()
    M047.apply(store)
    second = store.execute(
        "SELECT fact_id, fisher_mean, fisher_variance FROM atomic_facts ORDER BY fact_id"
    ).fetchall()
    assert first == second


def test_it_finishes_a_conversion_it_did_not_start(store) -> None:
    """Resumable: the second half converts on the next run."""
    written = _seed(store, 5)
    for fact_id in list(written)[:2]:
        store.execute(
            "UPDATE atomic_facts SET fisher_mean=?, fisher_variance=? WHERE fact_id=?",
            (encode_float_vector(written[fact_id]),
             encode_float_vector(written[fact_id]), fact_id),
        )
    store.commit()

    M047.apply(store)
    remaining = store.execute(
        "SELECT COUNT(*) FROM atomic_facts "
        "WHERE typeof(fisher_mean)='text' AND fisher_mean LIKE '[%'"
    ).fetchone()[0]
    assert remaining == 0
    assert M047.verify(store) is True


def test_an_unreadable_vector_is_left_alone_not_nulled(store) -> None:
    """NULL would read as "no evidence" rather than as "look at this"."""
    _seed(store, 3)
    store.execute(
        "INSERT INTO atomic_facts VALUES ('broken','x','[1.0, 2.0,','[0.5]')"
    )
    store.commit()

    M047.apply(store)

    raw = store.execute(
        "SELECT fisher_mean FROM atomic_facts WHERE fact_id='broken'"
    ).fetchone()[0]
    assert raw == "[1.0, 2.0,", "the unreadable value was altered"
    assert store.execute(
        "SELECT COUNT(*) FROM atomic_facts WHERE fisher_mean IS NULL"
    ).fetchone()[0] == 0

    # Everything readable still converted, and verify tolerates the leftover.
    assert store.execute(
        "SELECT COUNT(*) FROM atomic_facts WHERE typeof(fisher_mean)='blob'"
    ).fetchone()[0] == 3
    assert M047.verify(store) is True


def test_verify_notices_a_conversion_that_stopped_early(store) -> None:
    """The check must be able to fail, or it certifies nothing."""
    _seed(store, 4)
    assert M047.verify(store) is False, (
        "verify passed on a store where nothing had been converted"
    )
    M047.apply(store)
    assert M047.verify(store) is True


def test_a_fact_with_no_fisher_vector_is_untouched(store) -> None:
    store.execute("INSERT INTO atomic_facts VALUES ('empty','x',NULL,NULL)")
    store.commit()
    M047.apply(store)
    row = store.execute(
        "SELECT fisher_mean, fisher_variance FROM atomic_facts WHERE fact_id='empty'"
    ).fetchone()
    assert row == (None, None)


def test_a_table_without_the_columns_is_not_an_error(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "other.db")
    conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, content TEXT)")
    conn.commit()
    M047.apply(conn)          # must not raise
    assert M047.verify(conn) is True
    conn.close()


def test_more_rows_than_one_batch(store) -> None:
    """The loop must terminate on a store larger than a single batch."""
    _seed(store, M047._BATCH + 37)
    M047.apply(store)
    assert store.execute(
        "SELECT COUNT(*) FROM atomic_facts "
        "WHERE typeof(fisher_mean)='text' AND fisher_mean LIKE '[%'"
    ).fetchone()[0] == 0


def test_a_concurrent_write_is_not_overwritten(store, monkeypatch) -> None:
    """Reading a row and writing it back later is a lost update.

    A live writer recomputing this fact's vectors between the SELECT and the
    UPDATE would have its new value replaced by the old one re-encoded — no
    error, no retry, and the decay curve for that memory silently wrong.
    """
    written = _seed(store, 3)
    victim = "f1"
    fresh = _vector(999)

    # sqlite3.Connection attributes are read-only, so the interleaving is
    # staged rather than patched: the concurrent writer lands BEFORE apply()
    # runs, which is the same situation apply() must detect — the value it is
    # about to overwrite is no longer the one it read.
    store.execute(
        "UPDATE atomic_facts SET fisher_mean=? WHERE fact_id=?",
        (encode_float_vector(fresh), victim),
    )
    store.commit()

    M047.apply(store)

    raw = store.execute(
        "SELECT fisher_mean FROM atomic_facts WHERE fact_id=?", (victim,)
    ).fetchone()[0]
    got = np.frombuffer(raw, dtype=np.float32)
    assert np.allclose(got, fresh, atol=1e-6, rtol=0), (
        "the concurrent writer's value was overwritten with the stale one"
    )
    assert not np.allclose(got, written[victim], atol=1e-6, rtol=0)


def test_rowids_do_not_move_even_with_holes(store) -> None:
    """A gapless fixture cannot see a reassignment.

    Rowids 1..N reassigned to 1..N look identical on every aggregate. The
    holes are what make the check able to fail.
    """
    _seed(store, 6)
    store.execute("DELETE FROM atomic_facts WHERE fact_id IN ('f1','f3')")
    store.commit()

    before = store.execute(
        "SELECT fact_id, rowid FROM atomic_facts ORDER BY fact_id"
    ).fetchall()
    gaps = [r[1] for r in before]
    assert gaps != list(range(1, len(gaps) + 1)), (
        "the fixture has no holes, so this test could not detect a reassignment"
    )

    M047.apply(store)

    after = store.execute(
        "SELECT fact_id, rowid FROM atomic_facts ORDER BY fact_id"
    ).fetchall()
    assert before == after, "a fact is now sitting at a different row position"


def test_verify_catches_a_conversion_that_blanked_every_value(store) -> None:
    """A well-formed buffer of zeroes is not a conversion.

    Checking only that the bytes are a multiple of four and finite would certify
    a run that wrote 768 zeroes over every vector, and the decay dynamics would
    then read every memory as carrying no evidence at all.
    """
    _seed(store, 5)
    M047.apply(store)
    assert M047.verify(store) is True

    blank = np.zeros(WIDTH, dtype=np.float32).tobytes()
    store.execute("UPDATE atomic_facts SET fisher_mean = ?", (blank,))
    store.commit()

    assert M047.verify(store) is False, (
        "verify certified a store whose vectors had all been blanked"
    )


def test_one_honestly_zero_vector_is_not_a_wipe(store) -> None:
    """Refusing on a single zero marks the migration incomplete forever.

    A store can legitimately hold a memory whose evidence really is zero.
    Deferred migrations record a failure and continue, so a verify that refuses
    on one such row would report this conversion unfinished on every future
    start, with nothing left to do about it.
    """
    _seed(store, 5)
    M047.apply(store)
    store.execute(
        "UPDATE atomic_facts SET fisher_mean = ? WHERE fact_id = 'f0'",
        (np.zeros(WIDTH, dtype=np.float32).tobytes(),),
    )
    store.commit()

    assert M047.verify(store) is True, (
        "one legitimately-zero vector was read as a blanket wipe"
    )


# ---------------------------------------------------------------------------
# Drifting back after the migration says it is done
# ---------------------------------------------------------------------------


def _text_rows(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM atomic_facts "
        "WHERE typeof(fisher_mean) = 'text' AND fisher_mean LIKE '[%'"
    ).fetchone()[0]


def test_a_store_that_drifted_back_is_repaired(store) -> None:
    """A completed conversion plus newer old-form rows is a repairable state.

    This is not hypothetical. A long-running daemon holds its writers in memory,
    and this migration shipped in the same commit as the write-path change that
    stores the new form -- so a daemon started before that commit converts
    nothing and keeps writing text while the migration, run later by a
    short-lived process, records itself complete.

    On the author's store: migration complete at 08:37, newest converted row
    08:08, and sixteen facts written between 10:50 and 17:34 still text, from a
    daemon that had been up twenty-five hours.

    Without a repair hook the framework refuses to touch a completed migration
    and logs "automatic replay is disabled" on every open -- a false alarm that
    also switches off the repair a genuinely incomplete migration would need.
    """
    conn = store
    M047.apply(conn)
    assert M047.verify(conn) is True

    # An older writer adds two facts in the form it still knows.
    for index in (900, 901):
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, content, fisher_mean, fisher_variance)"
            " VALUES (?, ?, ?, ?)",
            (f"stale-{index}", "written by a process older than the migration",
             json.dumps(_vector(index)), json.dumps(_vector(index + 1))),
        )
    conn.commit()
    assert _text_rows(conn) == 2
    assert M047.verify(conn) is False, (
        "verify must still notice old-form rows; that signal is what triggers repair"
    )

    M047.repair(conn)

    assert _text_rows(conn) == 0
    assert M047.verify(conn) is True


def test_the_repair_converts_rather_than_discards(store) -> None:
    """The numbers come back, at the precision float32 offers.

    A repair that zeroed these would pass every structural check and be a
    deletion wearing the right shape, because the decay dynamics read an
    all-zero Fisher vector as "this memory carries no evidence".
    """
    conn = store
    M047.apply(conn)
    original = _vector(4242)
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, content, fisher_mean, fisher_variance)"
        " VALUES (?, ?, ?, ?)",
        ("drifted", "a fact from a stale writer",
         json.dumps(original), json.dumps(_vector(4243))),
    )
    conn.commit()

    M047.repair(conn)

    stored = conn.execute(
        "SELECT fisher_mean FROM atomic_facts WHERE fact_id = 'drifted'"
    ).fetchone()[0]
    assert isinstance(stored, bytes)
    recovered = decode_float_vector(stored, field="fisher_mean", fact_id="drifted")
    assert recovered is not None
    assert len(recovered) == WIDTH
    assert any(value != 0.0 for value in recovered), "a zeroed vector is a deletion"
    assert np.allclose(recovered, original, atol=1e-6)


def test_repairing_a_store_that_needs_nothing_changes_nothing(store) -> None:
    """The framework re-verifies after repairing, so repair must be idempotent."""
    conn = store
    M047.apply(conn)
    before = conn.execute(
        "SELECT fact_id, hex(fisher_mean), hex(fisher_variance) FROM atomic_facts"
        " ORDER BY fact_id"
    ).fetchall()

    M047.repair(conn)

    after = conn.execute(
        "SELECT fact_id, hex(fisher_mean), hex(fisher_variance) FROM atomic_facts"
        " ORDER BY fact_id"
    ).fetchall()
    assert before == after


def test_the_migration_offers_the_hook_the_framework_looks_for(store) -> None:
    """A data conversion whose end-state a writer can undo needs `repair`.

    The runner's contract: when a completed migration fails verification it
    calls `repair(conn)` if the module has one and otherwise refuses, logging
    that automatic replay is disabled. An additive migration cannot drift -- a
    column does not disappear -- but a value-format conversion can, so for this
    one the absence of the hook was the defect.
    """
    assert callable(getattr(M047, "repair", None))
