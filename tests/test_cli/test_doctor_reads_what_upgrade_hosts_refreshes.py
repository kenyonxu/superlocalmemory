# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""After `slm upgrade-hosts --host codex --apply`, doctor reports the new version.

4.1.25 audit L-21: the upgrade refreshes the assets under ``~/.agents/skills``
and ``~/.codex/agents``; doctor read Codex's own plugin cache under
``~/.codex/plugins``, which the upgrade never touches, and kept reporting the
old version. The upgrade now stamps the version it wrote, and doctor reads it.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from superlocalmemory.cli import doctor_hosts
from superlocalmemory.cli.host_upgrades import _upgrade_host
from superlocalmemory.hooks import codex_assets


def _package_version() -> str:
    from importlib.metadata import version

    return version("superlocalmemory")


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return fake


#: Never equal to any real release, so the cache copy is always the older one,
#: even in a dev environment whose installed metadata lags the source tree.
STALE = "0.0.1"


def _stale_plugin_cache(home: Path, version: str = STALE) -> None:
    folder = home / ".codex" / "plugins" / "cache" / "qualixar" / "superlocalmemory-codex" / version
    (folder / ".codex-plugin").mkdir(parents=True)
    (folder / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "superlocalmemory-codex", "version": version}), encoding="utf-8")


def _refresh(home: Path) -> dict:
    return _upgrade_host("codex", apply=True, already_integrated=True)


def test_before_the_upgrade_doctor_sees_the_old_plugin_cache(home) -> None:
    _stale_plugin_cache(home)
    assert doctor_hosts.installed_plugin_versions() == {"superlocalmemory-codex": STALE}


def test_the_upgrade_records_the_version_it_wrote(home) -> None:
    result = _refresh(home)
    assert result["status"] == "updated", result
    assert codex_assets.read_version_stamp(home) == _package_version()
    assert codex_assets.status_assets(home=home)["version"] == _package_version()


def test_after_the_upgrade_doctor_reports_the_refreshed_version(home) -> None:
    _stale_plugin_cache(home)
    _refresh(home)
    found = doctor_hosts.installed_plugin_versions()
    assert found == {"superlocalmemory-codex": _package_version()}


def test_the_older_cache_copy_is_named_not_counted_as_stale(home) -> None:
    _stale_plugin_cache(home)
    result = _refresh(home)
    assert result["status"] == "updated", result
    found = doctor_hosts.installed_plugin_versions()
    note = doctor_hosts.codex_cache_note(found, home)
    assert note, (found, codex_assets.read_version_stamp(home))
    note = doctor_hosts.codex_cache_note(doctor_hosts.installed_plugin_versions(), home)
    assert STALE in note and "upgrade-hosts" in note and _package_version() in note


def test_no_note_when_the_cache_matches_or_is_absent(home) -> None:
    _refresh(home)
    assert doctor_hosts.codex_cache_note(doctor_hosts.installed_plugin_versions(), home) == ""
    _stale_plugin_cache(home, _package_version())
    assert doctor_hosts.codex_cache_note(doctor_hosts.installed_plugin_versions(), home) == ""


def test_a_preview_stamps_nothing(home) -> None:
    result = _upgrade_host("codex", apply=False, already_integrated=True)
    assert result["status"] == "preview"
    assert codex_assets.read_version_stamp(home) is None


def test_removing_the_assets_removes_the_stamp(home) -> None:
    _refresh(home)
    codex_assets.remove_assets(home=home)
    assert codex_assets.read_version_stamp(home) is None


def test_a_codex_only_machine_is_detected_through_the_stamp(home) -> None:
    _refresh(home)
    assert list(doctor_hosts.installed_plugin_versions()) == ["superlocalmemory-codex"]


def test_an_older_stamp_is_reported_stale_with_the_codex_command() -> None:
    assert doctor_hosts.update_command_for({"superlocalmemory-codex": "4.1.0"}) \
        == "slm upgrade-hosts --host codex --apply"
    both = doctor_hosts.update_command_for(
        {"claude:superlocalmemory@qualixar": "4.1.0", "superlocalmemory-codex": "4.1.0"})
    assert "claude plugin update" in both and "slm upgrade-hosts --host codex --apply" in both


def _plugin_row(monkeypatch) -> dict:
    from tests.helpers.doctor import run_doctor_report

    captured: list[dict] = []
    with patch("superlocalmemory.cli.json_output.json_print",
               side_effect=lambda event, data=None, **kw: captured.append(data or {})):
        run_doctor_report(Namespace(json=True, quick=True))
    return {c["name"]: c for c in captured[-1]["checks"]}["plugin_skills"]


def test_slm_doctor_warns_before_and_passes_after(home, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "data"))
    _stale_plugin_cache(home)
    before = _plugin_row(monkeypatch)
    assert before["status"] == "WARN" and f"superlocalmemory-codex={STALE}" in before["detail"]
    assert before["fix"] == "slm upgrade-hosts --host codex --apply"

    _refresh(home)
    after = _plugin_row(monkeypatch)
    assert after["status"] == "PASS", after
    assert STALE in after["detail"]  # named as a note, not counted
