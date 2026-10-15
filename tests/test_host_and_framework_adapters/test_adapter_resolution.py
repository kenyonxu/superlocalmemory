"""UX-18/19/22: validate distribution boundaries before installation."""

import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ("google-adk", "crewai", "semantic-kernel")


@pytest.mark.parametrize("name", ADAPTERS)
def test_adapters_do_not_embed_conflicting_slm_runtime(name):
    project = tomllib.loads((ROOT / f"ide/integrations/{name}/pyproject.toml").read_text())[
        "project"
    ]
    assert not any(d.startswith("superlocalmemory") for d in project["dependencies"])


def test_ux19_crewai_declares_upstream_python_boundary():
    project = tomllib.loads((ROOT / "ide/integrations/crewai/pyproject.toml").read_text())[
        "project"
    ]
    spec = SpecifierSet(project["requires-python"])
    assert "3.13" in spec
    assert "3.14" not in spec


@pytest.mark.slow
@pytest.mark.parametrize("name", ADAPTERS)
def test_native_adapter_resolves(name, tmp_path):
    path = ROOT / f"ide/integrations/{name}/pyproject.toml"
    project = tomllib.loads(path.read_text())["project"]
    inputs = [str(path)]
    if any(d.startswith("superlocalmemory") for d in project["dependencies"]):
        inputs.append(str(ROOT / "pyproject.toml"))
    result = subprocess.run(
        [
            "uv",
            "pip",
            "compile",
            *inputs,
            "--python-version",
            "3.13",
            "--output-file",
            str(tmp_path / "resolved.txt"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-8000:]
