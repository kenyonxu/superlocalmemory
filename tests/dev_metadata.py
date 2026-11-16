"""Cheap preflight for the editable distribution used by repository tests."""

import tomllib
from importlib import metadata
from pathlib import Path

import pytest


def check_installed_version() -> None:
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    try:
        # Fork identity (4.3.0): the distribution is mslm-memory even though the
        # imported module keeps the name superlocalmemory.
        installed = metadata.version("mslm-memory")
    except metadata.PackageNotFoundError:
        installed = "missing"
    if installed != expected:
        raise pytest.UsageError(
            f"Installed mslm-memory metadata is {installed}; "
            f"pyproject.toml requires {expected}. Repair this editable install: "
            "uv sync --frozen --reinstall-package mslm-memory"
        )
