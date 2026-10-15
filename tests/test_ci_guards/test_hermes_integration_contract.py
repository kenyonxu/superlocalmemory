"""Hermes evidence describes the shipped native plugin without inventing MCP config."""

from pathlib import Path

import pytest
import yaml
from scripts import integration_compatibility as compat

ROOT = Path(__file__).resolve().parents[2]


def test_hermes_is_a_native_package_with_honest_contract_levels():
    manifest = compat.load_manifest(ROOT / "ide/integration-contracts.json")
    clients = {c["id"]: c for c in manifest["clients"]}
    hermes = clients["hermes"]
    assert hermes["test_level"] == "local_packaged_contract"
    assert not hermes["external_host_runtime_proven"]
    assert hermes["contracts"]["start_command_shape"]["status"] == "proven_local"
    assert hermes["contracts"]["start_command_shape"]["checker"] == "hermes_config"
    assert hermes["contracts"]["memory_surface"]["status"] == "static_only"
    assert {
        "hermes-plugin/plugin.yaml", "hermes-plugin/__init__.py",
        "hermes-plugin/command-inventory.json", "hermes-plugin/remote.py",
    } <= set(hermes["artifacts"])


def test_hermes_package_checker_parses_shipped_artifacts(tmp_path):
    assert compat._hermes_package(ROOT, "hermes", tmp_path) == "hermes-plugin/plugin.yaml"


def test_hermes_package_checker_rejects_unimplemented_hooks(tmp_path):
    import shutil

    shutil.copytree(ROOT / "hermes-plugin", tmp_path / "hermes-plugin")
    manifest = tmp_path / "hermes-plugin/plugin.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["provides_hooks"].append("on_invented_event")
    manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(AssertionError, match="hook"):
        compat._hermes_package(tmp_path, "hermes", tmp_path)
