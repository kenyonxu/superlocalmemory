# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The check has to be where the write is, not beside it.

A validation endpoint the caller may or may not have called is not a guard. The
route that persists the embedding model is the only place a mismatched vector
width can actually be stopped, so the refusal lives there and returns 409.

It fails open on anything it cannot determine — a store with no vectors, a model
server that is not running — because refusing on "I could not tell" would block
a legitimate first-time setup, which is the common case.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.server.routes.v3_api import _refuse_incompatible_embedding


class _Emb:
    def __init__(self, provider="", ollama_model="", ollama_base_url=""):
        self.provider = provider
        self.ollama_model = ollama_model
        self.ollama_base_url = ollama_base_url


class _Config:
    def __init__(self, base_dir: Path, provider: str = ""):
        self.base_dir = base_dir
        self.embedding = _Emb(provider=provider)


def _store_holding(tmp_path: Path, width: int | None) -> Path:
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute(
        "CREATE TABLE embedding_metadata (vec_rowid INTEGER PRIMARY KEY,"
        " fact_id TEXT, model_name TEXT, dimension INTEGER)"
    )
    if width is not None:
        conn.execute(
            "INSERT INTO embedding_metadata VALUES (1,'f','m',?)", (width,)
        )
    conn.commit()
    conn.close()
    return tmp_path


def test_a_different_width_is_refused(tmp_path) -> None:
    _store_holding(tmp_path, 768)
    result = _refuse_incompatible_embedding(
        _Config(tmp_path), _Emb(), "mxbai-embed-large", 1024,
    )
    assert result is not None, "a 1024-wide model was accepted into a 768 store"
    assert result.status_code == 409
    body = result.body.decode()
    assert "768" in body and "1024" in body
    assert "slm db migrate" in body, "refusing without saying how to proceed"


def test_the_same_width_is_allowed(tmp_path) -> None:
    _store_holding(tmp_path, 768)
    assert _refuse_incompatible_embedding(
        _Config(tmp_path), _Emb(), "some-other-768d-model", 768,
    ) is None


def test_a_store_with_no_vectors_can_take_anything(tmp_path) -> None:
    _store_holding(tmp_path, None)
    assert _refuse_incompatible_embedding(
        _Config(tmp_path), _Emb(), "mxbai-embed-large", 1024,
    ) is None


def test_no_store_at_all_is_not_a_refusal(tmp_path) -> None:
    assert _refuse_incompatible_embedding(
        _Config(tmp_path / "nothing-here"), _Emb(), "mxbai-embed-large", 1024,
    ) is None


def test_a_broken_check_does_not_block_a_save(tmp_path, monkeypatch) -> None:
    """Failing open is deliberate; failing closed would lock users out."""
    class Exploding:
        @property
        def base_dir(self):
            raise RuntimeError("cannot resolve the data root")
        embedding = _Emb()

    assert _refuse_incompatible_embedding(
        Exploding(), _Emb(), "anything", 1024,
    ) is None
