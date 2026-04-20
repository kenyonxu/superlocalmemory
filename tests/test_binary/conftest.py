"""Make ``scripts/`` importable for this test package only.

We intentionally do NOT add an ``__init__.py`` to ``scripts/`` — that
would make it a subpackage of nothing, and could pollute install
paths. Instead we ``sys.path.insert`` on the scripts directory so
``import build_entry`` / ``import release_manifest`` resolves.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
