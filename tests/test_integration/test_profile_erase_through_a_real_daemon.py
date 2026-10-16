# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Erasing a whole profile through a REAL daemon, after real canonical writes (UX-31, OPS-11).

A store built by inserting facts directly has no write receipts, which is why the
collision between the erasure sweep and the immutable receipt ledger was never
seen: it only exists once the daemon has accepted a save. So this saves through
the daemon, switches to the profile, erases it through the dashboard's route, and
checks that the response, the stored receipt and the files all say the same thing.
"""

from __future__ import annotations

import json
import uuid

from tests.test_integration.test_erasure_integrity_e2e import (  # noqa: F401
    ERASE_PROFILE,
    _count,
    _log_copies,
    _remember_complete,
    _ro,
    _text_copies,
    daemon,
    stub_embedder,
)


def test_a_real_profile_erases_completely_and_every_surface_agrees(
        daemon) -> None:  # noqa: F811 - the fixture, imported above
    tag = uuid.uuid4().hex[:6]
    marker = f"zorvexa{tag}"
    _remember_complete(
        daemon, f"{marker} Haverlin audits the Quillmarsh depot ledger.", ERASE_PROFILE)
    assert _count(daemon, "SELECT COUNT(*) FROM write_commits WHERE profile_id = ?",
                  (ERASE_PROFILE,)) >= 1, "precondition: the daemon left write receipts"
    assert _text_copies(daemon, marker), "precondition: the words are stored"

    code, body = daemon.request("POST", f"/api/profiles/{ERASE_PROFILE}/switch")
    assert code == 200, body
    code, body = daemon.request("POST", "/api/compliance/gdpr/erase", {"confirm": ERASE_PROFILE})

    assert code == 200, body
    # Whatever the verdict, the pieces agree with each other.
    receipt = _ro(daemon, "SELECT state, all_erased FROM erasure_receipts "
                  "WHERE profile_id = ? AND subject_type = 'profile'", (ERASE_PROFILE,))
    assert receipt, "no receipt"
    stored_complete = receipt[-1] == ("COMPLETE", 1)
    said = (body.get("error"), body.get("erasure_report"), receipt[-1])
    assert body["success"] == stored_complete == bool(body["erasure_report"]["complete"]), said
    assert body["success"] is True and stored_complete, said
    assert body["active_profile"] == ERASE_PROFILE
    code, status = daemon.request("GET", "/api/profiles")
    assert status["active_profile"] != ERASE_PROFILE, "still working on the erased profile"

    assert not body.get("table_delete_failures"), body
    assert body["residue_rows"] == 0 and body["write_receipts"] >= 1, body
    assert _count(daemon, "SELECT COUNT(*) FROM write_commits WHERE profile_id = ?",
                  (ERASE_PROFILE,)) == 0
    assert _text_copies(daemon, marker) == {}
    assert _log_copies(daemon, marker) == []

    code, listing = daemon.request("GET", "/api/profiles")
    assert ERASE_PROFILE not in json.dumps(listing), "the dashboard listing brought it back"
    assert ERASE_PROFILE not in (daemon.data_root / "profiles.json").read_text()
