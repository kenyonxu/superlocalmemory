"""Explicitly tolerate a red doctor's exit in tests that inspect report rows."""

from argparse import Namespace


def run_doctor_report(args: Namespace) -> None:
    """Keep the report available on exit 1; propagate every other exit code."""
    from superlocalmemory.cli.commands import cmd_doctor

    try:
        cmd_doctor(args)
    except SystemExit as exc:
        if exc.code != 1:
            raise
