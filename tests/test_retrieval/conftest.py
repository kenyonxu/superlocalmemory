# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Fixtures shared by the retrieval tests."""

from __future__ import annotations

import pytest

from tests._engine_closer import closing_retrieval_engines


@pytest.fixture(autouse=True)
def closes_retrieval_engines(monkeypatch):
    """Close every ``RetrievalEngine`` built by any test in this folder.

    See ``tests/_engine_closer.py``: a new test cannot leak an engine's
    channel or query-embed pool by forgetting to opt in.
    """
    yield from closing_retrieval_engines(monkeypatch)
