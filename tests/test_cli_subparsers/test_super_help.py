"""Super-help (`slm help`) completeness + topics (v3.8.2 UX-7).

Guarantees:
1. Every top-level command registered in the CLI parser appears in the
   grouped `slm help` overview — so adding a command without documenting it
   fails CI (drift guard).
2. Focused topics (modes / config / self-heal) render without error.
3. The no-topic overview lists the groups + the self-healing section.
"""
from __future__ import annotations

import re
from argparse import Namespace
from pathlib import Path

import superlocalmemory.cli.commands as cmds


def _top_level_commands() -> set[str]:
    """Command names registered directly on the top-level subparser (`sub`).

    Nested subparsers use other variables (db_sub, session_sub, …), so the
    ``\\bsub\\.add_parser`` anchor selects only top-level commands.

    WIDENED in 4.0.7. Scanning only main.py made this guard blind to a command
    registered from its own module: ``summary`` is attached by
    ``summary_cmd.register_summary_parser(sub)``, so ``sub.add_parser("summary")``
    lives in summary_cmd.py and main.py never mentions the name. The command was
    therefore exempt from the very drift check that exists to catch it — a
    contributor could add a whole command group, document nothing, and stay
    green. Any cli/*.py that calls ``sub.add_parser`` is now scanned.
    """
    cli_dir = Path(cmds.__file__).parent
    names: set[str] = set()
    for path in sorted(cli_dir.glob("*.py")):
        names |= set(re.findall(
            r"\bsub\.add_parser\(\s*[\"']([a-z0-9\-]+)[\"']",
            path.read_text(encoding="utf-8"),
        ))
    return names


def test_super_help_covers_every_command():
    top = _top_level_commands()
    assert top, "parsed no top-level commands from main.py"
    missing = top - cmds.all_help_commands()
    assert not missing, f"slm help omits commands: {sorted(missing)}"


def test_help_topics_render_without_error():
    for topic in ("modes", "config", "self-heal", "health", "mode"):
        cmds.cmd_help(Namespace(topic=topic))  # must not raise


def test_help_unknown_topic_is_graceful(capsys):
    cmds.cmd_help(Namespace(topic="does-not-exist"))
    out = capsys.readouterr().out
    assert "No help topic" in out


def test_help_overview_lists_groups_and_selfheal(capsys):
    cmds.cmd_help(Namespace(topic=None))
    out = capsys.readouterr().out
    assert "command overview" in out
    assert "self-healing" in out.lower()
    assert "doctor --fix" in out
    # A representative command from each of a few groups is present.
    for cmd in ("remember", "recall", "doctor", "setup", "mesh", "loop"):
        assert re.search(rf"\b{cmd}\b", out), f"{cmd} missing from overview"
