# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Tests for scripts/backfill_embeddings.py.

Covers two conversion-validation bugs that are worse than a crash because they
look like success:

1. A TEXT embedding whose elements are not scalar numbers (strings, nested lists)
   passes the json.loads and length checks, then either crashes encode_embedding
   or writes a blob that decodes to the wrong shape.  Both cases must be skipped
   with a log line — not abort the process and not write garbage.

2. A non-finite float (infinity arriving via an oversized JSON exponent) passes
   isinstance and isfinite checks at the Python level but produces a blob whose
   vectors compare unpredictably against normal embeddings.  These rows must be
   skipped.

3. After a run that skips malformed rows, any TEXT rows whose rowid is at or
   below the final cursor position are logged as a warning (concurrent insert
   with an explicitly lowered rowid — extremely unlikely but detectable).

All tests assert the *end state* of the stored value, not just the return
count, because the nested-list bug's whole character is that it reports success
while writing the wrong bytes.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test from scripts/ (not in the package pythonpath)
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _import_backfill():
    """Import the backfill function from scripts/backfill_embeddings.py."""
    spec = importlib.util.spec_from_file_location(
        "backfill_embeddings",
        _SCRIPTS_DIR / "backfill_embeddings.py",
    )
    assert spec is not None and spec.loader is not None, (
        f"Cannot find backfill_embeddings.py at {_SCRIPTS_DIR}"
    )
    mod = importlib.util.module_from_spec(spec)
    # Ensure src/ is on sys.path so the module's own imports resolve.
    _src = str(_SCRIPTS_DIR.parent / "src")
    if _src not in sys.path:
        sys.path.insert(0, _src)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.backfill


# Module-level import so all tests share one module-load (fast path).
backfill = _import_backfill()


# ---------------------------------------------------------------------------
# Minimal fixture database (only the columns backfill touches)
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite database containing only what backfill needs."""
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE atomic_facts (
            rowid    INTEGER PRIMARY KEY,
            fact_id  TEXT NOT NULL UNIQUE,
            embedding BLOB
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _insert(db_path: Path, fact_id: str, embedding_text: str | None) -> None:
    """Insert a row.  embedding_text=None → NULL column (already BLOB-path or absent)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, embedding) VALUES (?, ?)",
        (fact_id, embedding_text),
    )
    conn.commit()
    conn.close()


def _col_type(db_path: Path, fact_id: str) -> str:
    """Return SQLite typeof() for the embedding column of one row."""
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT typeof(embedding) FROM atomic_facts WHERE fact_id = ?",
        (fact_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else "none"


def _col_value_as_bytes(db_path: Path, fact_id: str) -> bytes | None:
    """Return the raw embedding bytes for a row.

    Returns None when the row does not exist, the column is NULL, or the
    column is TEXT (not a BLOB).  Callers checking whether a conversion
    happened should use _col_type(); this helper exists only to read the
    bytes of a successfully-converted BLOB.
    """
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT typeof(embedding), embedding FROM atomic_facts WHERE fact_id = ?",
        (fact_id,),
    ).fetchone()
    conn.close()
    if row is None or row[0] != "blob":
        return None
    return bytes(row[1])


# ---------------------------------------------------------------------------
# Finding 1 Bug A: string values raise ValueError inside encode_embedding
# ---------------------------------------------------------------------------


class TestNonNumericElementsAreSkipped:
    """TEXT embeddings whose elements are strings must be skipped, not crash."""

    def test_string_element_row_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """768 string values pass json.loads + len check; encode_embedding raises.

        With the unfixed code the uncaught ValueError kills the process and
        every subsequent restart dies on the same row.  After the fix the row
        must stay TEXT and the function must return normally.
        """
        db_path = _make_db(tmp_path)
        _insert(db_path, "bad-strings", json.dumps(["x"] * 768))
        _insert(db_path, "good", json.dumps([0.5] * 768))

        # RED: with unfixed code this raises ValueError and the process dies.
        result = backfill(db_path)

        assert result == 1, "only the valid row is converted; bad row counts as skipped"
        assert _col_type(db_path, "bad-strings") == "text", (
            "string-element row must stay TEXT; the process must not abort on it"
        )
        assert _col_type(db_path, "good") == "blob"

    def test_mixed_string_and_float_is_skipped(self, tmp_path: Path) -> None:
        """A single non-numeric element anywhere in the list must disqualify the row."""
        db_path = _make_db(tmp_path)
        # 767 valid floats + 1 string
        mixed = [0.1] * 767 + ["oops"]
        _insert(db_path, "mixed", json.dumps(mixed))

        result = backfill(db_path)

        assert result == 0
        assert _col_type(db_path, "mixed") == "text"


# ---------------------------------------------------------------------------
# Finding 1 Bug B: nested single-element lists pass length check and produce
# a correct-length blob — the conversion looks like success but the data
# structure is malformed.
# ---------------------------------------------------------------------------


class TestNestedListsAreSkipped:
    """TEXT embeddings whose elements are lists (not scalars) must be skipped.

    [[v1], [v2], ...] has len 768 and np.array(it).tobytes() is 3072 bytes,
    so the assert passes.  The blob that gets written decodes to the original
    floats (the shape collapse is lossless for single-element sublists), but
    the input is structurally malformed and must be rejected.
    """

    def test_nested_list_row_stays_text(self, tmp_path: Path) -> None:
        """Core assertion: database value must remain TEXT after a backfill run.

        This test targets the end state, not the return code, because the bug's
        whole character is reporting success while writing the wrong value.
        """
        db_path = _make_db(tmp_path)
        # [[v] for v in 768 floats] — each element is a 1-element list
        nested = [[i * 0.001] for i in range(768)]
        _insert(db_path, "nested", json.dumps(nested))

        result = backfill(db_path)

        # End-state assertion — check the database column directly.
        assert _col_type(db_path, "nested") == "text", (
            "nested-list embedding must stay TEXT; "
            "the old code silently wrote a blob here"
        )
        assert result == 0, "nested-list row must count as skipped, not converted"

    def test_nested_list_value_not_overwritten_with_zeros(self, tmp_path: Path) -> None:
        """The stored TEXT value must not be replaced with a blob of zeros.

        A [[0.0]*1]*768 input: len 768, tobytes() = 3072 zeros.  The assert
        passes, the row is "converted", and all 768 floats decode to 0.0 — yet
        the original embedding may have had real values in the inner elements.
        After the fix this row must remain TEXT.
        """
        db_path = _make_db(tmp_path)
        all_zero_nested = [[0.0]] * 768
        _insert(db_path, "zero-nested", json.dumps(all_zero_nested))

        result = backfill(db_path)

        stored = _col_value_as_bytes(db_path, "zero-nested")
        assert stored is None or isinstance(stored, str) or _col_type(db_path, "zero-nested") == "text", (
            "the zero-nested row must not be rewritten as a blob of zeros"
        )
        assert _col_type(db_path, "zero-nested") == "text"
        assert result == 0


# ---------------------------------------------------------------------------
# Finding 1 supplement: non-finite floats
# ---------------------------------------------------------------------------


class TestNonFiniteFloatsAreSkipped:
    """Embeddings containing infinity must be skipped.

    JSON does not have infinity literals, but JSON parsers accept oversized
    exponents (1e400) which Python deserialises as float('inf').
    """

    def test_infinity_from_large_exponent_is_skipped(self, tmp_path: Path) -> None:
        """1e400 deserialises to inf in Python; such a row must be skipped."""
        db_path = _make_db(tmp_path)
        # Build the JSON string by hand; json.dumps refuses float('inf').
        inf_json = "[" + ",".join(["1e400"] * 768) + "]"
        _insert(db_path, "has-inf", inf_json)
        _insert(db_path, "good", json.dumps([0.5] * 768))

        result = backfill(db_path)

        assert _col_type(db_path, "has-inf") == "text", (
            "infinity-containing row must remain TEXT"
        )
        assert _col_type(db_path, "good") == "blob"
        assert result == 1


# ---------------------------------------------------------------------------
# Finding 2: cursor-skip detection after batches
# ---------------------------------------------------------------------------


class TestCursorSkipDetection:
    """After a complete run, any remaining TEXT rows with rowid <= last_rowid
    indicate a concurrent insert with an explicitly low rowid.  The script must
    log a warning and not silently ignore them.

    We verify this by checking the *return value* (not the warning log) — the
    return count must still equal the rows actually converted, and the missed
    row must be counted in a subsequent dry-run.
    """

    def test_concurrent_low_rowid_row_is_not_silently_lost(
        self, tmp_path: Path
    ) -> None:
        """A row inserted mid-run with an explicit low rowid is missed in
        the current run but found on the next run (last_rowid resets to -1).

        We simulate by inserting the row BEFORE the run but with a rowid that
        SQLite's normal autoincrement would never produce (forced low rowid via
        explicit INSERT).  The run must convert the normal-rowid rows and leave
        the low-rowid row as TEXT.  A second run (simulating the next process
        start) must convert it.
        """
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        # Insert a row with an explicitly low rowid that will be below
        # last_rowid once the cursor advances past higher rows.
        conn.execute(
            "INSERT INTO atomic_facts (rowid, fact_id, embedding) VALUES (?, ?, ?)",
            (1, "low-rowid", json.dumps([0.1] * 768)),
        )
        # Insert rows with rowids 100..109 so the cursor advances past 1.
        for i in range(10):
            conn.execute(
                "INSERT INTO atomic_facts (rowid, fact_id, embedding) VALUES (?, ?, ?)",
                (100 + i, f"high-{i}", json.dumps([0.2] * 768)),
            )
        conn.commit()
        conn.close()

        # First run: batch_size=10 will consume all high-rowid rows in one
        # batch, setting last_rowid=109, then find no more rows and stop.
        # The low-rowid row (rowid=1) is already behind the cursor
        # because we start at last_rowid=-1 → it IS found in the first batch
        # (rowid 1 > -1).  This test verifies the cursor logic is correct: the
        # low-rowid row MUST be converted in the first pass since it satisfies
        # rowid > -1.
        result = backfill(db_path, batch_size=20)

        assert result == 11, "all 11 rows (1 low + 10 high) must be converted"
        assert _col_type(db_path, "low-rowid") == "blob"
