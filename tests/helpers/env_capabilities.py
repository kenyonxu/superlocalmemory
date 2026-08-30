# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""What the interpreter running these tests can actually do.

Some of this suite needs capabilities that are a property of how Python was
BUILT, not of what is installed. When one is missing the tests that need it fail
somewhere deep and unhelpfully — two of them failed on a vector count being 0,
four rows of stack away from the reason.

This names the capability once so a missing one reads as "this interpreter
cannot do X, here is how to get one that can" instead of as a product defect.
"""

from __future__ import annotations

import sqlite3


def sqlite_can_load_extensions() -> bool:
    """Whether this interpreter's sqlite3 can load a loadable extension.

    ``enable_load_extension`` is compiled in or it is not: a Python built
    against a SQLite without ``SQLITE_ENABLE_LOAD_EXTENSION`` does not have the
    method at all. Nothing installable fixes it — the interpreter has to change.

    Without it ``sqlite_vec`` cannot load, so ``VectorStore.available`` is False,
    the engine's vector store is None, and every "is this findable by meaning"
    question answers no. The product degrades correctly; the tests that assert
    the non-degraded path cannot run.
    """
    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        return False
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.NotSupportedError, sqlite3.OperationalError):
        return False
    else:
        return True
    finally:
        conn.close()


def vector_search_available() -> bool:
    """Whether semantic-vector search can actually run here."""
    if not sqlite_can_load_extensions():
        return False
    try:
        import sqlite_vec
    except Exception:
        return False
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute("CREATE VIRTUAL TABLE t USING vec0(embedding float[4])")
        return True
    except Exception:
        return False
    finally:
        conn.close()


#: Reason string for ``skipif``, naming the remedy rather than the symptom.
NO_VECTOR_SEARCH_REASON = (
    "this interpreter cannot load SQLite extensions, so sqlite-vec is "
    "unavailable and the vector store is disabled. Rebuild the test "
    "environment with a Python whose sqlite3 has enable_load_extension "
    "(the homebrew python@3.14 on this machine does): "
    "python3.14 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'"
)
