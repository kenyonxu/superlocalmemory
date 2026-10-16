"""A stale editable install must fail before it can invalidate release tests."""

from importlib import metadata

import pytest

from tests.dev_metadata import check_installed_version


def test_current_editable_install_matches_project():
    check_installed_version()


@pytest.mark.parametrize("installed", ["4.1.24", None])
def test_stale_or_missing_metadata_names_the_repair(monkeypatch, installed):
    def version(name):
        assert name == "superlocalmemory"
        if installed is None:
            raise metadata.PackageNotFoundError(name)
        return installed

    monkeypatch.setattr(metadata, "version", version)
    repair = "uv sync --frozen --reinstall-package superlocalmemory"
    with pytest.raises(pytest.UsageError, match=repair):
        check_installed_version()
