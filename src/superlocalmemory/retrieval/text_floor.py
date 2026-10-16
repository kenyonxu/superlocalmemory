# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Read this space's floor live; removing calibration restores the actual default."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def text_semantic_floor(embedder: Any, default: float) -> float:
    from superlocalmemory.core.embedding_recipe import TASK_SUFFIXES
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.retrieval.embedding_calibration import NOMIC_BOOTSTRAP_FLOOR, floor_for
    from superlocalmemory.runtimes.media_models import EG2_REPO, text_min_semantic_for

    config = getattr(embedder, "_config", None)
    name = getattr(embedder, "model_name", None) or getattr(config, "model_name", None)
    if not isinstance(name, str):
        return default
    dimension = getattr(embedder, "dimension", None) or getattr(config, "dimension", None)
    if isinstance(dimension, int):
        root = getattr(embedder, "_calibration_root", None)
        if not isinstance(root, (str, Path)):
            root = state_path("answer_check_calibration.json").parent
        signature = getattr(embedder, "_calibration_signature", None)
        if not isinstance(signature, str):
            signature = f"{name}::{dimension}"
        measured = floor_for(Path(root), signature)
        if measured is not None:
            return measured
    if name.endswith(TASK_SUFFIXES):
        return NOMIC_BOOTSTRAP_FLOOR if default == 0.60 else default
    if name == EG2_REPO:
        return default  # an unmeasured model constant cannot override store policy
    own = text_min_semantic_for(name)
    return default if own is None else own
