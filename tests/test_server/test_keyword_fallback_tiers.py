# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The keyword fallback (a recall past its budget) speaks the same tiers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from superlocalmemory.retrieval import support_gate as sg
from superlocalmemory.retrieval import support_tiers as st
from superlocalmemory.server import recall_fallback

ROWS = [{"fact_id": "a", "content": "Hub v0.3.4 fixed the resultType bug.", "confidence": 1},
        {"fact_id": "b", "content": "v0.3.4 build passed", "confidence": 1},
        {"fact_id": "c", "content": "v0.3.4 tagged", "confidence": 1}]


def _engine():
    return SimpleNamespace(_db=SimpleNamespace(), profile_id="default",
                           _config=SimpleNamespace())


def _admit_all(rows, db, pid, **kw):
    """A stand-in store has no sources to check; the tiers are under test."""
    from superlocalmemory.storage.models import RecallResponse

    return rows, RecallResponse(source_fidelity_status="checked")


def _run(query, rows=ROWS, **kw):
    from superlocalmemory.retrieval import fallback_fidelity

    with patch.object(recall_fallback, "_fetch_candidates", lambda *a: list(rows)), \
            patch.object(fallback_fidelity, "admit_fallback", _admit_all):
        return recall_fallback.recall_keyword_fallback(_engine(), query, 1, **kw)


def test_supported_rows_are_kept_and_labelled() -> None:
    out = _run("What did hub v0.3.4 fix?")
    assert [r["fact_id"] for r in out["results"]] == ["a"]
    assert out["results"][0]["tier"] == st.SUPPORTED and out["tier"] == st.SUPPORTED


def test_it_over_fetches_before_it_filters() -> None:
    seen = []
    with patch.object(recall_fallback, "_fetch_candidates",
                      lambda db, pid, q, n: seen.append(n) or list(ROWS)):
        recall_fallback.recall_keyword_fallback(_engine(), "q", 1)
    assert seen and seen[0] > 1


def test_related_and_weak_rows_only_on_request_with_a_plain_note() -> None:
    plain = _run("v0.3.4 snazzle")
    assert plain["results"] == [] and plain["tier"] == st.RELATED
    assert plain["abstention_note"] == recall_fallback.TIMEOUT_NOTE
    assert "isn't in SuperLocalMemory" not in plain["abstention_note"]
    related = _run("v0.3.4 snazzle", include_related=True)
    assert [r["tier"] for r in related["results"]] == [st.RELATED]
    weak = _run("v0.3.4 snazzle", show_weak=True)
    assert len(weak["results"]) == 1  # still at most ``limit``


def test_common_words_of_other_languages_are_not_rare_terms() -> None:
    note = "Die nächtliche Sicherung läuft um 03:00 Uhr."
    assert sg.lexical_support("Wann läuft die nächtliche Sicherung?", note)
    assert sg.lexical_support("Quando o relatório mensal é enviado?",
                              "O relatório mensal é enviado no primeiro dia útil.")


def test_unspaced_scripts_are_matched_on_character_pairs() -> None:
    assert sg.lexical_support("日志保留多久？", "我们的日志保留三十天。")
    assert not sg.lexical_support("ケブホシ", "我们的日志保留三十天。")
