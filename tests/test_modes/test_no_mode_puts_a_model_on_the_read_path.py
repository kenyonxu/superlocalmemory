# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Enrichment happens when a memory is written, never when one is read.

Modes B and C buy a better memory by spending a model at write time. Spending
one at read time would cost the two things that are not negotiable: a recall
that answers inside its budget, and a store that does not phone anywhere.

The read path does have one internal model round and one optional remote
reranker. Both are real features with real users — a deployment with no capable
client in front of it, and a corpus the bundled English reranker cannot read.
Both must be **off unless asked for**, and that is what is pinned here, by
resolving the shipped configuration rather than by reading the code.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from superlocalmemory.core import recall_pipeline
from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.recall_pipeline import resolve_hot_path_fast


def test_a_default_install_skips_the_internal_model_round() -> None:
    """Unset means "you are the reasoner", which is the shipped default."""
    config = SLMConfig()
    assert resolve_hot_path_fast(None, config) is True, (
        "a default install would run a model on every recall"
    )


def test_asking_for_it_explicitly_still_works() -> None:
    """A deployment with no smart client in front can still opt in."""
    config = SLMConfig()
    assert resolve_hot_path_fast(False, config) is False
    assert resolve_hot_path_fast(True, config) is True


def test_a_default_install_has_no_remote_reranker() -> None:
    config = SLMConfig()
    backend = getattr(config.retrieval, "cross_encoder_backend", "")
    endpoint = getattr(config.retrieval, "cross_encoder_endpoint", "")
    assert backend not in ("openai", "remote"), (
        f"the shipped cross-encoder backend is {backend!r}, which is a network call"
    )
    assert not endpoint, f"a remote reranker endpoint ships configured: {endpoint!r}"


def test_the_only_thing_fast_decides_is_the_model_round() -> None:
    """It has been documented as skipping channels. It never has.

    Parsed rather than grepped, because the first textual match for a name is
    its import, not its use.
    """
    source = inspect.getsource(recall_pipeline.run_recall)
    tree = ast.parse(source.lstrip())

    uses: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "fast" and isinstance(node.ctx, ast.Load):
            uses.append("load")

    # Whatever else it does, it must never decide which channels run.
    disabling = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "extra_disabled_channels"
    ]
    assert disabling, "run_recall no longer passes extra_disabled_channels at all"
    for kw in disabling:
        assert isinstance(kw.value, (ast.Constant, ast.Name)), (
            "which channels run is now computed; if that is deliberate, prove "
            "here that `fast` is not an input to it"
        )
        if isinstance(kw.value, ast.Name):
            assert kw.value.id != "fast", "`fast` now disables channels"


@pytest.mark.parametrize(
    "path",
    sorted(
        p for p in (Path(__file__).resolve().parents[2] / "src" / "superlocalmemory"
                    / "core").glob("recall*.py")
    ),
    ids=lambda p: p.name,
)
def test_no_cloud_client_is_constructed_while_reading(path: Path) -> None:
    """A cloud SDK imported on the read path is one refactor from being called."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"openai", "anthropic", "cohere", "google.generativeai"}
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    hits = sorted(set(imported) & forbidden)
    assert not hits, f"{path.name} imports {hits} on the read path"
