"""Each test has a store outside any project rooted at tmp_path."""

import pytest


@pytest.fixture(autouse=True)
def private_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path.parent / ("slm-" + tmp_path.name)))
