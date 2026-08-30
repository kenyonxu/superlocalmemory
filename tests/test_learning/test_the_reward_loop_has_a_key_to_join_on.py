"""The two halves of the reward loop have to agree on what names a session.

A recall records an outcome; a later tool event is the evidence that outcome is
settled from. They join on ``session_id``, and for a long time they could not:
the ids on each side came from different namespaces, so no outcome ever found
its evidence and no signal was ever registered.

The cause was one prefix. ``core/engine.py`` minted ``engine:<pid>`` by hand
instead of through ``synthetic_session_id``, so ``SYNTHETIC_PREFIXES`` never
listed it and ``is_conversation`` returned True for the daemon's own process
id. The module's docstring had warned about exactly this — "a new front cannot
invent a third prefix that continuity silently accepts" — and the guard it
describes only works for prefixes that are actually registered.
"""

from __future__ import annotations

import os

from superlocalmemory.core.session_identity import (
    SYNTHETIC_PREFIXES,
    is_conversation,
    synthetic_session_id,
)


def test_the_daemons_own_process_id_is_not_a_conversation():
    assert is_conversation(f"engine:{os.getpid()}", "default") is False


def test_every_front_that_invents_an_id_is_registered():
    """Every prefix a front in this package invents for its own bookkeeping."""
    for observed in ("engine:17089", "cli:18213", "http:1787511373686", "mcp:claude"):
        assert is_conversation(observed, "default") is False, observed


def test_an_id_a_caller_actually_gave_still_counts():
    """The guard must not swallow the ids that make the loop work: a
    real client id has this shape."""
    assert is_conversation("01a01bba-5f09-7a61-86f5-d67511c89283", "default") is True


def test_the_engine_mints_its_ambient_id_through_the_helper():
    """A hand-built f-string is how the prefix escaped registration; if this
    reverts, is_conversation goes quietly wrong again rather than loudly."""
    import inspect

    from superlocalmemory.core import engine as engine_module

    source = inspect.getsource(engine_module)
    assert 'f"engine:{os.getpid()}"' not in source
    assert "synthetic_session_id(" in source


def test_the_helper_produces_something_the_guard_rejects():
    """The round trip the codebase depends on: anything minted as invented must
    read back as invented."""
    for kind in ("engine", "cli", "http", "mcp", "probe"):
        assert kind + ":" in SYNTHETIC_PREFIXES
        assert is_conversation(synthetic_session_id(kind, "1"), "default") is False


def _minted_session_prefixes():
    """Every literal prefix an f-string mints *as a session id*, by AST.

    A line-level regex is not enough: this package builds idempotency keys,
    cache keys and error codes the same way, and flagging those would make the
    guard noise that gets deleted. The binding has to name a session — either
    the assignment target does, or the function returning it does.
    """
    import ast
    import re
    from pathlib import Path

    import superlocalmemory

    root = Path(superlocalmemory.__file__).parent
    found: list[tuple[str, str]] = []

    def literal_prefix(node):
        """The ``kind:`` head of an f-string, when it has one."""
        if not isinstance(node, ast.JoinedStr) or not node.values:
            return None
        head = node.values[0]
        if not isinstance(head, ast.Constant) or not isinstance(head.value, str):
            return None
        match = re.fullmatch(r"([a-z_]+):", head.value)
        return match.group(1) + ":" if match else None

    def names_a_session(text: str) -> bool:
        return "session" in text.lower()

    for path in sorted(root.rglob("*.py")):
        if "test" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_names_session = names_a_session(func.name)
            for node in ast.walk(func):
                prefix = None
                if isinstance(node, ast.Return) and func_names_session:
                    prefix = literal_prefix(node.value)
                elif isinstance(node, ast.Assign):
                    targets = " ".join(
                        t.id if isinstance(t, ast.Name)
                        else getattr(t, "attr", "") for t in node.targets
                    )
                    if names_a_session(targets):
                        prefix = literal_prefix(node.value)
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    target = getattr(node.target, "id", "") or getattr(
                        node.target, "attr", "")
                    if names_a_session(target):
                        prefix = literal_prefix(node.value)
                if prefix:
                    found.append((str(path.relative_to(root)), prefix))
    return found


def test_no_front_mints_a_session_prefix_the_guard_does_not_know():
    """The enforcement the module asks for but never had.

    ``session_identity`` says a new front "cannot invent a third prefix that
    continuity silently accepts" — but nothing checked, and more prefixes were
    in use than were registered. One filed every unnamed recall under the
    daemon's own process id; another pooled unrelated callers under a single
    shared id. Both read as real conversations, so continuity ranked on them
    and the reward pipeline tried to join on them.

    This walks the source rather than a hand-written list, because the two that
    escaped did so precisely by not being on one.
    """
    offenders = [
        f"{where}: {prefix}"
        for where, prefix in _minted_session_prefixes()
        if prefix not in SYNTHETIC_PREFIXES
    ]
    assert not offenders, (
        "a session-id prefix is minted but not registered in "
        f"SYNTHETIC_PREFIXES, so is_conversation() will accept it as a real "
        f"conversation: {offenders}"
    )


def test_the_guard_actually_finds_the_known_fronts():
    """A scanner that finds nothing would pass the test above forever.

    ``cli:`` and ``mcp:`` are still built as f-strings, so the scanner must see
    both. ``engine:`` and ``agent:`` are deliberately absent: those two sites
    now call ``synthetic_session_id`` instead, which is the end state this
    guard is pushing every front towards — a prefix that goes through the
    helper cannot be unregistered, because the helper is what the registry
    describes.
    """
    prefixes = {prefix for _where, prefix in _minted_session_prefixes()}
    assert {"cli:", "mcp:"} <= prefixes, prefixes
