"""The shared bridge must ship as Python source on platforms without git symlinks."""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name,package",
    [
        ("google-adk", "google_adk_superlocalmemory"),
        ("crewai", "crewai_superlocalmemory"),
        ("semantic-kernel", "semantic_kernel_superlocalmemory"),
    ],
)
def test_bridge_is_included_by_build_backend(name, package):
    metadata = tomllib.loads((ROOT / "ide/integrations" / name / "pyproject.toml").read_text())
    hook = metadata["tool"]["hatch"]["build"]["hooks"]["custom"]
    assert hook == {"path": "hatch_build.py", "package": package}
    assert not (ROOT / "ide/integrations" / name / package / "_engine_bridge.py").is_symlink()


@pytest.mark.slow
@pytest.mark.parametrize("name", ["google-adk", "crewai", "semantic-kernel"])
def test_source_distribution_contains_bridge(name, tmp_path):
    import subprocess
    import tarfile

    root = ROOT / "ide/integrations" / name
    built = subprocess.run(
        ["uv", "build", str(root), "--sdist", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert built.returncode == 0, built.stderr
    archive = next(tmp_path.glob("*.tar.gz"))
    with tarfile.open(archive) as contents:
        assert any(n.endswith("/_engine_bridge.py") for n in contents.getnames())
        contents.extractall(tmp_path / "source", filter="data")
    source = next((tmp_path / "source").iterdir())
    rebuilt = subprocess.run(
        ["uv", "build", str(source), "--wheel", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rebuilt.returncode == 0, rebuilt.stderr
    import zipfile

    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        assert any(n.endswith("/_engine_bridge.py") for n in wheel.namelist())
