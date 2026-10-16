"""Cheap preflight for the editable distribution used by repository tests."""

import tomllib
from importlib import metadata
from pathlib import Path

import pytest


def check_installed_version() -> None:
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    try:
        installed = metadata.version("superlocalmemory")
    except metadata.PackageNotFoundError:
        installed = "missing"
    if installed != expected:
        raise pytest.UsageError(
            f"Installed superlocalmemory metadata is {installed}; "
            f"pyproject.toml requires {expected}. Repair this editable install: "
            "uv sync --frozen --reinstall-package superlocalmemory"
        )
