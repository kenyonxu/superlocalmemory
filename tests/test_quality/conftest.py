# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Register the answerability corpus module by name before these tests exec it.

The corpus module defines a frozen dataclass with PEP 563 string annotations
(``from __future__ import annotations``); Python 3.13's dataclasses resolves
those against ``sys.modules[cls.__module__]`` while the class body runs, so a
module executed via ``spec.loader.exec_module`` without being registered under
its import name crashes before the first test line runs. Registering first is
the same pattern the eval itself uses (``_load_corpus_module``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _register(name: str) -> None:
    if name in sys.modules:
        return
    path = _ROOT / "scripts" / "quality" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


_register("answerability_corpus")
