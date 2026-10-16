# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The answerability harness measures the embedding model it is told to.

The answer-support recipe was fitted on nomic only, because the harness could
build its store with nomic only. On EmbeddingGemma 2 that recipe refused correct
answers (best support 0.008 against a 0.30 bar on the author's store, 13 Oct
2026). A recipe per model needs a harness per model, and a nomic store must
never be reused for a Gemma measurement.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "quality" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_nomic_stays_the_default_and_unchanged(tmp_path):
    ev = _load("answerability_eval")
    config = ev._config("a", tmp_path)
    assert config.embedding.provider == "sentence-transformers"
    assert "nomic" in config.embedding.model_name


def test_gemma_builds_the_managed_text_model(tmp_path):
    ev = _load("answerability_eval")
    config = ev._config("a", tmp_path, embedder="gemma")
    assert config.embedding.model_name == "google/embeddinggemma-2"
    assert config.embedding.provider == "slm-media"
    assert config.embedding.dimension == 768


def test_each_model_gets_its_own_store(tmp_path):
    ev = _load("answerability_eval")
    nomic = ev.store_dir(tmp_path, "d1", "nomic")
    gemma = ev.store_dir(tmp_path, "d1", "gemma")
    assert nomic == tmp_path / "d1"  # the existing cache stays valid
    assert gemma != nomic


def test_an_unknown_model_is_refused(tmp_path, capsys):
    ev = _load("answerability_eval")
    assert ev.main(["--work", str(tmp_path), "--embedder", "word2vec"]) == 2
    with pytest.raises(ValueError):
        ev._config("a", tmp_path, embedder="word2vec")
