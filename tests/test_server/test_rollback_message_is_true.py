# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The rollback's 202 says what a rollback does.

4.2.1 answered `slm embedder rollback` with "re-indexing 27415 memories with X
in the background", the sentence a forward switch earns. A rollback does not
re-index: it copies back the vectors the previous model already made for every
memory that has not changed (`embedding_reindex_steps.copy_previous`, no model
call) and embeds only what is new or edited since. On the author's store that
finished in minutes, so the 27,415-memory wording sent him looking for a
multi-hour job that was not running.
"""

from __future__ import annotations

import json

from superlocalmemory.server.routes.embedding_reindex import _accepted


def _detail(job: dict) -> str:
    return json.loads(_accepted(job).body)["detail"]


def _job(kind: str, **extra) -> dict:
    return {"kind": kind, "total": 27415, "from": "embeddinggemma-2::768",
            "to": "nomic-embed-text-v1.5::768", **extra}


def test_a_switch_still_says_it_is_re_indexing_every_memory():
    detail = _detail(_job("switch"))
    assert "re-indexing 27415 memories with nomic-embed-text-v1.5::768" in detail


def test_a_rollback_does_not_claim_a_full_re_index():
    detail = _detail(_job("rollback", reusable=27300))
    assert "re-indexing" not in detail
    assert "27415" in detail and "27300" in detail
    assert "copied back" in detail and "no model call" in detail
    assert "embeddinggemma-2::768" in detail  # recall keeps using it until the swap
    assert "slm embedder status" in detail


def test_a_rollback_without_a_count_still_tells_the_truth():
    detail = _detail(_job("rollback"))
    assert "re-indexing" not in detail
    assert "copied back" in detail
    assert "up to" not in detail  # no number it does not have
