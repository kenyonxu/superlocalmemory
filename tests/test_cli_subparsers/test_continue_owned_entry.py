"""Continue reconnect replaces every owned duplicate and legacy SLM entry."""

from pathlib import Path

import pytest
import yaml
from scripts.integration_compatibility import _extract_slm_block

from superlocalmemory.hooks.portable_kit import IDE_MATRIX, connect_ide


@pytest.mark.parametrize("duplicates", [1, 2])
@pytest.mark.parametrize("current", [False, True])
def test_continue_migrates_legacy_and_collapses_owned_servers(tmp_path, duplicates, current):
    path = tmp_path / ".continue/config.yaml"
    path.parent.mkdir()
    other = {"name": "other", "command": "other"}
    legacy_other = {"name": "mcp", "params": {"serverName": "other"}}
    legacy_slm = {"name": "mcp", "params": {"serverName": "superlocalmemory"}}
    data = {
        "name": "User config", "version": "2.0.0", "schema": "v1", "models": [],
        "contextProviders": [legacy_other, legacy_slm, legacy_slm],
        "mcpServers": [other] + ([{"name": "superlocalmemory", "command": "slm"}] * duplicates
                                 if current else []),
    }
    path.write_text(yaml.safe_dump(data))
    first = connect_ide("continue", home=tmp_path)
    assert first["error"] is None
    rendered = yaml.safe_load(path.read_text())
    assert rendered["contextProviders"] == [legacy_other]
    assert rendered["mcpServers"][0] == other
    assert rendered["name"] == "User config"
    assert _extract_slm_block(rendered, IDE_MATRIX["continue"])["name"] == "superlocalmemory"
    before = path.read_bytes()
    assert connect_ide("continue", home=tmp_path)["mcp_config"] == "unchanged"
    assert path.read_bytes() == before


def test_host_upgrade_detects_continue_current_schema(tmp_path):
    from superlocalmemory.cli.host_upgrades import _detected_hosts

    result = connect_ide("continue", home=tmp_path)
    assert result["error"] is None
    assert "continue" in _detected_hosts(tmp_path)


def test_legacy_setup_json_generator_also_pins_runtime(tmp_path):
    from superlocalmemory.hooks.ide_connector import IDEConnector

    path = tmp_path / "client.json"
    assert IDEConnector(home=tmp_path)._merge_json(path)
    import json
    import sys

    block = json.loads(path.read_text())["mcpServers"]["superlocalmemory"]
    assert block["command"] == sys.executable
    assert block["args"] == ["-m", "superlocalmemory.cli", "mcp"]


@pytest.mark.parametrize("legacy_id,client_id", [("continue_dev", "continue"), ("zed", "zed")])
def test_setup_alias_uses_the_same_host_generator(tmp_path, legacy_id, client_id):
    from scripts.integration_compatibility import _parse_config, _validate_start_command

    from superlocalmemory.hooks.ide_connector import IDEConnector

    assert IDEConnector(home=tmp_path).connect(legacy_id)
    desc = IDE_MATRIX[client_id]
    path = tmp_path / desc.mcp_path_global
    assert path.exists()
    _validate_start_command(_extract_slm_block(_parse_config(path, desc.fmt), desc))


@pytest.mark.parametrize("client_id", [key for key, desc in IDE_MATRIX.items() if desc.fmt])
def test_every_config_generator_round_trips_a_pinned_start_command(tmp_path, client_id):
    from scripts.integration_compatibility import _portable_config

    assert _portable_config(Path(__file__).resolve().parents[2], client_id, tmp_path)


def test_continue_collapses_duplicates_without_a_legacy_provider(tmp_path):
    path = tmp_path / ".continue/config.yaml"
    path.parent.mkdir()
    result = connect_ide("continue", home=tmp_path)
    assert result["error"] is None
    data = yaml.safe_load(path.read_text())
    data["mcpServers"].append(data["mcpServers"][0].copy())
    path.write_text(yaml.safe_dump(data))
    assert connect_ide("continue", home=tmp_path)["mcp_config"] == "merged"
    assert len(yaml.safe_load(path.read_text())["mcpServers"]) == 1
