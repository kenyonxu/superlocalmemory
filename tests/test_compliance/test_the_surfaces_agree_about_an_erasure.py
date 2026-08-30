# Copyright (c) 2026 Varun Pratap Bhardwaj
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Two surfaces reporting the same erasure must not disagree about it.

The HTTP route inlined its own list of failure markers and the command line
used a different one, so for the same erasure the API could return failure while
the terminal printed COMPLETE and exited zero. An operator answering a
regulator would get a different answer depending on which one they ran.

There are genuinely two questions — whether the data is gone, and whether that
can be shown afterwards — and both surfaces now read both.
"""

from __future__ import annotations

import pytest

from superlocalmemory.server.routes.compliance import _erasure_succeeded


def test_a_clean_erasure_succeeds() -> None:
    assert _erasure_succeeded({"erasure_complete": 1, "erasure_provable": 1})


def test_an_unpersisted_receipt_is_not_success() -> None:
    """The data is gone and it cannot be shown. That is not a pass."""
    assert not _erasure_succeeded({
        "erasure_complete": 1, "erasure_provable": 0, "receipt_persist_failed": 1,
    })


@pytest.mark.parametrize(
    "marker",
    [
        "vector_store_failures",
        "audit_completion_failed",
        "audit_request_failed",
        "receipt_persist_failed",
        "table_delete_failures",
        "code_graph_failed",
        "fact_expansion_fts_failed",
        "working_sets_failed",
        "residue_recount_failed",
        "backup_scan_failed",
    ],
)
def test_every_recorded_failure_is_a_failure(marker: str) -> None:
    assert not _erasure_succeeded({"erasure_complete": 1, "erasure_provable": 1, marker: 1})


def test_a_failed_expansion_index_is_not_hidden() -> None:
    """The entity route returned success even when this errored."""
    assert not _erasure_succeeded({
        "erasure_complete": 1, "fact_expansion_fts_failed": 1,
    })


def test_a_result_without_the_verdicts_is_judged_on_its_markers() -> None:
    """Their absence must not read as failure — the entity path had none."""
    assert _erasure_succeeded({"atomic_facts": 3, "graph_edges": 7})
    assert not _erasure_succeeded({"atomic_facts": 3, "code_graph_failed": 1})
