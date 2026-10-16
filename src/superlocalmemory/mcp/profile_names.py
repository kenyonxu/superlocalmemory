# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Validation of MCP tool-profile names (``SLM_MCP_PROFILE``, ``--profile``).

Profile names are a closed set: the canonical names, ``whole`` for every
tool, and the count-suffixed aliases earlier releases published. Anything
else is refused (fail closed, so a typo never widens the tool surface), but
refused with a sentence that lists the valid names rather than a traceback.
"""

from __future__ import annotations

import os
import sys

from superlocalmemory.mcp.profiles import _PROFILE_ALIASES, _PROFILE_DEFINITIONS

WHOLE = "whole"


class UnknownProfileError(ValueError):
    """A profile name that is neither canonical nor a published alias."""


def valid_profile_names() -> tuple[str, ...]:
    """Canonical names in documentation order, ending with ``whole``."""
    return (*_PROFILE_DEFINITIONS, WHOLE)


def canonical_profile(name: str) -> str:
    """Return the canonical profile for *name* ("" means no selection).

    Raises:
        UnknownProfileError: for any name SLM does not define.
    """
    wanted = (name or "").strip().lower()
    canonical = _PROFILE_ALIASES.get(wanted, wanted)
    if not canonical or canonical == WHOLE or canonical in _PROFILE_DEFINITIONS:
        return canonical
    raise UnknownProfileError(unknown_profile_message(name))


def unknown_profile_message(name: str, source: str = "SLM_MCP_PROFILE") -> str:
    valid = ", ".join(valid_profile_names())
    return (
        f"{source}={name!r} is not recognised; valid profiles: {valid} "
        f"({WHOLE} registers every tool)."
    )


def exit_on_unknown_profile() -> None:
    """Refuse to start ``slm mcp`` with an unknown ``SLM_MCP_PROFILE``.

    Writes to stderr only: stdout is the MCP JSON-RPC channel.
    """
    raw = os.environ.get("SLM_MCP_PROFILE", "")
    try:
        canonical_profile(raw)
    except UnknownProfileError as exc:
        print(f"slm mcp: {exc}", file=sys.stderr)
        sys.exit(2)


# -- The saved tool set (``slm tools set``) ---------------------------------
#
# Plugins and extensions start ``slm mcp`` from host configs a user should not
# have to edit. One choice saved in SLM's data folder applies to every host;
# an explicit SLM_MCP_PROFILE in a host's config still wins.

TOOL_SET_FILE = "mcp_tools.json"
DEFAULT = "default"


def _data_root(root=None):
    from pathlib import Path

    if root is not None:
        return Path(root)
    from superlocalmemory.infra.data_root import canonical_data_root

    return canonical_data_root()


def saved_tool_set(root=None) -> str:
    """The saved canonical tool set, or "" when none (or an unreadable one) is saved.

    A damaged or unknown entry reads as "" (the default), never as a wider set.
    """
    import json

    try:
        data = json.loads((_data_root(root) / TOOL_SET_FILE).read_text(encoding="utf-8"))
        return canonical_profile(str(data.get("tool_set", "")))
    except (OSError, ValueError, AttributeError):
        return ""


def save_tool_set(name: str, root=None) -> str:
    """Save *name* (``default`` clears it) and return the canonical value saved."""
    import json
    import os

    wanted = (name or "").strip().lower()
    canonical = "" if wanted == DEFAULT else canonical_profile(wanted)
    path = _data_root(root) / TOOL_SET_FILE
    if not canonical:
        path.unlink(missing_ok=True)
        return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"tool_set": canonical}) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return canonical


def effective_profile(environ=None, root=None) -> str:
    """SLM_MCP_PROFILE when the host sets one, else the saved choice, else ""."""
    env = os.environ if environ is None else environ
    explicit = (env.get("SLM_MCP_PROFILE") or "").strip().lower()
    return explicit if explicit else saved_tool_set(root)
