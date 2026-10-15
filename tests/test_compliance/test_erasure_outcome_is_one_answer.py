# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Every surface reports an erasure the same way, and says what was and was not erased.

OPS-14: the command line printed ``success: true`` for an erasure whose own body
said ``erasure_complete: false`` and then exited 1. UX-31: the dashboard showed
"Erase failed: unknown" under a receipt that said COMPLETE. One verdict
(``compliance.erasure_outcome``) now feeds the JSON envelope, the printed text,
the exit code, the HTTP body and the stored receipt.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from superlocalmemory.compliance import erasure_outcome as outcome
from superlocalmemory.server.routes.compliance import _erasure_response
from tests.conftest import force_sync_enrichment
from tests.test_compliance.erasure_universe import (
    OTHER,
    PROFILE,
    Universe,
    _seed_backup,
    _seed_engine,
)

COMPLETE = {"memories": 3, "atomic_facts": 3, "erasure_complete": 1, "erasure_provable": 1}


def test_a_complete_erasure_is_a_success_with_nothing_remaining() -> None:
    assert outcome.erasure_succeeded(COMPLETE)
    statement = outcome.describe(COMPLETE)
    assert statement["complete"] and statement["provable"] and statement["remains"] == []
    assert statement["removed"] == {"memories": 3, "atomic_facts": 3}


@pytest.mark.parametrize("marker", [
    "residue_rows", "table_delete_failures", "write_receipts_failed", "optimize_cache_failed",
    "profile_registry_failed", "backup_obligations_pending", "backup_snapshots_unreadable",
    "wal_checkpoint_failed", "some_future_step_failed", "vector_store_failures",
])
def test_any_marker_of_something_left_makes_it_a_failure(marker) -> None:
    result = {**COMPLETE, marker: 2}
    assert not outcome.erasure_succeeded(result)
    statement = outcome.describe(result)
    assert [item["step"] for item in statement["remains"]] == [marker]
    assert statement["remains"][0]["what"] and statement["remains"][0]["retry"]
    assert marker not in statement["removed"]
    assert outcome.failure_message(result).startswith("The erasure is not complete.")


def test_an_unwritten_receipt_is_not_success_but_the_data_is_reported_gone() -> None:
    result = {**COMPLETE, "erasure_provable": 0, "receipt_persist_failed": 1}
    assert not outcome.erasure_succeeded(result)
    statement = outcome.describe(result)
    assert statement["provable"] is False
    assert outcome.remaining_markers(result, data_only=True) == []


def test_the_entity_path_has_no_verdict_keys_and_is_judged_on_markers() -> None:
    assert outcome.erasure_succeeded({"facts": 2, "entity": 1})
    assert not outcome.erasure_succeeded({"facts": 2, "projection_failed": 1})


def test_the_subject_reference_hides_the_name_and_is_stable() -> None:
    ref = outcome.subject_ref("entity", "Alice Zebrowski")
    assert "alice" not in ref.lower() and ref == outcome.subject_ref("entity", " alice zebrowski ")
    assert ref != outcome.subject_ref("entity", "Bob")


def test_the_http_body_carries_the_statement_and_an_error_when_incomplete() -> None:
    body = _erasure_response("active_profile", "p1", {**COMPLETE, "backup_obligations_pending": 1})
    assert body["success"] is False
    assert "Backups still hold" in body["error"]
    assert body["erasure_report"]["remains"][0]["step"] == "backup_obligations_pending"
    assert body["active_profile"] == "p1"

    ok = _erasure_response("active_profile", "p1", COMPLETE)
    assert ok["success"] is True and "error" not in ok


# ----------------------------------------------------------------------------- CLI

@pytest.fixture
def store(engine_with_mock_deps, monkeypatch):
    engine = force_sync_enrichment(engine_with_mock_deps)
    for pid in (PROFILE, OTHER):
        engine._db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)", (pid, pid))
    root = engine._db.db_path.parent
    monkeypatch.setenv("SLM_DATA_DIR", str(root))
    u = Universe(engine=engine, root=root)
    _seed_engine(u)
    return u


def _erase(capsys):
    from superlocalmemory.cli import gdpr_cmd

    args = Namespace(profile=PROFILE, yes=True, dry_run=False, json=True)
    try:
        gdpr_cmd._cmd_gdpr_erase(args)
        code = 0
    except SystemExit as stop:
        code = int(stop.code or 0)
    out = capsys.readouterr()
    return code, json.loads(out.out)


def test_the_cli_says_success_false_for_an_incomplete_erasure(store, capsys) -> None:
    _seed_backup(store)  # a backup now holds the profile: the erasure cannot be complete

    code, env = _erase(capsys)

    assert code == 1
    assert env["success"] is False, "the envelope said success=true for an incomplete erasure"
    assert env["error"]["code"] == "ERASURE_INCOMPLETE"
    assert env["data"]["erasure_complete"] is False
    assert any(item["step"] == "backup_obligations_pending" for item in env["data"]["remains"])
    assert env["data"]["removed"], "what WAS erased is still reported"


def test_the_cli_says_success_true_only_when_it_is_complete(store, capsys) -> None:
    code, env = _erase(capsys)

    assert code == 0
    assert env["success"] is True and "error" not in env
    assert env["data"]["erasure_complete"] is True and env["data"]["remains"] == []


def test_the_cli_text_names_what_remains(store, capsys) -> None:
    from superlocalmemory.cli import gdpr_cmd

    _seed_backup(store)
    with pytest.raises(SystemExit) as stop:
        gdpr_cmd._cmd_gdpr_erase(Namespace(profile=PROFILE, yes=True, dry_run=False, json=False))
    text = capsys.readouterr().out

    assert stop.value.code == 1
    assert "INCOMPLETE" in text and "COMPLETE\n" not in text.replace("INCOMPLETE", "")
    assert "Backups still hold" in text


def test_an_opt_in_traffic_capture_is_reported_not_silently_kept(store) -> None:
    from superlocalmemory.compliance.gdpr import GDPRCompliance

    (store.root / "optimize_capture.jsonl").write_text('{"request": "anything"}\n')

    result = GDPRCompliance(
        store.engine._db, engine=store.engine, data_root=store.root).forget_profile(PROFILE)

    assert result["optimize_capture_present"] == 1 and result["erasure_complete"] == 0
    assert any("optimize_capture.jsonl" in item["what"]
               for item in result["erasure_report"]["remains"])
