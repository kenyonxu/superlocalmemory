# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""An erased workspace does not come back (OPS-13).

A workspace's name and description live in the database AND in ``profiles.json``.
Erasure deleted the database row only; the dashboard's profile listing then
"repaired" the database from the leftover entry, so the workspace reappeared
within one page load, with the description that may have named a person.
"""

from __future__ import annotations

import json

import pytest

from superlocalmemory.compliance.gdpr import GDPRCompliance
from superlocalmemory.core import profile_tombstones as tombstones
from superlocalmemory.core.profiles import ProfileManager
from superlocalmemory.server.routes import helpers
from tests.conftest import force_sync_enrichment

NAME = "erasecase"
DESCRIPTION = "Workspace for the Zebrowski divorce"


@pytest.fixture
def store(engine_with_mock_deps, monkeypatch):
    engine = force_sync_enrichment(engine_with_mock_deps)
    root = engine._db.db_path.parent
    monkeypatch.setenv("SLM_DATA_DIR", str(root))
    engine._db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name, description) VALUES (?, ?, ?)",
        (NAME, NAME, DESCRIPTION))
    manager = ProfileManager(root)
    manager.create_profile(NAME, description=DESCRIPTION)
    return engine, root, manager


def _erase(engine, root):
    return GDPRCompliance(engine._db, engine=engine, data_root=root).forget_profile(NAME)


def test_the_entry_and_its_description_leave_profiles_json(store) -> None:
    engine, root, _ = store
    assert DESCRIPTION in (root / "profiles.json").read_text()

    result = _erase(engine, root)

    assert result["profile_registry"] == 1 and not result.get("profile_registry_failed")
    text = (root / "profiles.json").read_text()
    assert NAME not in text and DESCRIPTION not in text


def test_an_erased_workspace_is_not_brought_back_by_the_dashboard_listing(store) -> None:
    engine, root, _ = store
    _erase(engine, root)

    listed = [p["profile_id"] for p in helpers.sync_profiles()]
    listed_again = [p["profile_id"] for p in helpers.sync_profiles()]

    assert NAME not in listed and NAME not in listed_again


def test_the_leftover_entry_of_an_older_erasure_is_dropped_not_resurrected(store) -> None:
    """A profiles.json written back after the erasure is cleaned on the next listing."""
    engine, root, _ = store
    _erase(engine, root)
    (root / "profiles.json").write_text(json.dumps({
        "active_profile": "default",
        "profiles": {"default": {"name": "default"}, NAME: {"name": NAME, "description": "back"}},
    }))

    assert NAME not in [p["profile_id"] for p in helpers.sync_profiles()]
    assert NAME not in (root / "profiles.json").read_text()


def test_a_profile_manager_loaded_before_the_erasure_does_not_write_it_back(store) -> None:
    engine, root, manager = store
    _erase(engine, root)

    manager.create_profile("another")  # any later save from the stale in-memory registry

    assert NAME not in (root / "profiles.json").read_text()


def test_the_active_pointer_falls_back_to_default(store) -> None:
    engine, root, manager = store
    manager.switch_profile(NAME)
    _erase(engine, root)

    data = json.loads((root / "profiles.json").read_text())
    assert data.get("active") != NAME and data.get("active_profile") != NAME


def test_the_tombstone_keeps_a_digest_not_the_name(store) -> None:
    engine, root, _ = store
    _erase(engine, root)

    text = (root / tombstones.TOMBSTONE_FILE).read_text()
    assert NAME not in text and tombstones.digest(NAME) in text


def test_creating_a_profile_of_that_name_on_purpose_works_again(store) -> None:
    engine, root, manager = store
    _erase(engine, root)

    helpers.ensure_profile_in_json(NAME, "a new workspace")  # the dashboard / CLI create path

    assert not tombstones.is_erased(root, NAME)
    assert NAME in (root / "profiles.json").read_text()
