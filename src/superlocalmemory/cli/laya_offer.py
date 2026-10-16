# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The answer check (Laya) is on by default where it can run (4.1.25).

Setup installs it unless the person opts out, unattended setup included; the
first command after an upgrade offers it the same way. Opting out:
``SLM_SKIP_LAYA=1`` (also true/yes/on), ``slm setup --no-laya``, the old
``SLM_INSTALL_LAYA=0``, or answering no once (remembered). It is never
installed on hardware it cannot run on, over an install someone already has,
or when the person set the check to off or to the hosted one.

``auto`` (the default check) picks a finished, checked install up by itself,
so nothing here needs to write the config. Run as a module, this is the
background install an unattended upgrade starts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any, TextIO

SKIP_ENV = "SLM_SKIP_LAYA"
#: The 4.1.18 opt-in. Installing is now the default; "0" still opts out.
LEGACY_ENV = "SLM_INSTALL_LAYA"
_YES = frozenset({"1", "true", "yes", "on"})
_NO = frozenset({"0", "false", "no", "off"})
DECLINED_FILE = "declined"
LOG_FILE = "install-after-upgrade.log"
#: Checks that may run Laya; "off" and "jev" are a person's choice to keep.
_LAYA_MODES = frozenset({"auto", "laya"})

NOT_OFFERED, DECLINED, INSTALLED, FAILED, STARTED = (
    "not_offered", "declined", "installed", "failed", "started")
OPT_OUT_HINT = f"Opt out with {SKIP_ENV}=1 or `slm setup --no-laya`."
LATER_HINT = "Set it up later in the dashboard: Settings → Answer check."


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().lower()


def opted_out(no_laya: bool = False) -> bool:
    return no_laya or _env(SKIP_ENV) in _YES or _env(LEGACY_ENV) in _NO


def supported() -> bool:
    """Apple Silicon only. Never raises."""
    try:
        from superlocalmemory.retrieval.sufficiency import laya_supported

        return bool(laya_supported())
    except Exception:
        return False


def _marker():
    from superlocalmemory.core import laya_runtime

    return laya_runtime.runtime_dir() / DECLINED_FILE


def declined() -> bool:
    try:
        return _marker().exists()
    except OSError:
        return False


def remember_decline() -> None:
    try:
        marker = _marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("declined at setup or upgrade\n", encoding="utf-8")
    except OSError:
        pass  # the worst case is being asked again


def forget_decline() -> None:
    try:
        _marker().unlink(missing_ok=True)
    except OSError:
        pass


def _needs_install(retrieval: Any) -> bool:
    from superlocalmemory.core import laya_runtime as lr

    status = lr.detect(retrieval)
    return status.state == lr.STATE_NOT_INSTALLED or (
        status.state == lr.STATE_FAILED and status.action == lr.ACTION_SETUP)


def wanted_after_upgrade(retrieval: Any) -> bool:
    if opted_out() or declined() or not supported():
        return False
    mode = str(getattr(retrieval, "sufficiency_judge", "auto") or "").strip().lower()
    return mode in _LAYA_MODES and _needs_install(retrieval)


def offer_after_upgrade(retrieval: Any, *, interactive: bool,
                        prompt: Callable[[str], str] = input,
                        out: TextIO | None = None) -> str:
    """Offer (interactive) or start (unattended) the install. Writes to
    ``out`` (stderr by default) only: stdout may be carrying a hook's JSON."""
    out = out or sys.stderr
    if not wanted_after_upgrade(retrieval):
        return NOT_OFFERED
    print("  New: the answer check lets your memory say \"I don't have that\"", file=out)
    print("  instead of guessing. About 1.1 GB, stays on this Mac.", file=out)
    if not interactive:
        started = start_background_install()
        print("  Setting it up in the background." if started else
              f"  Couldn't start it. {LATER_HINT}", file=out)
        print(f"  {OPT_OUT_HINT}", file=out)
        return STARTED if started else FAILED
    answer = (prompt("  Set it up now? [Y/n] (default: Y): ") or "y").strip().lower()
    if answer not in ("y", "yes"):
        remember_decline()
        print(f"  ✓ Skipped. {LATER_HINT}", file=out)
        return DECLINED
    return INSTALLED if _install_here(out) else FAILED


def _install_here(out: TextIO) -> bool:
    from superlocalmemory.core import laya_runtime as lr

    def _progress(fraction: float, step: str) -> None:
        print(f"    [{fraction * 100:5.1f}%] {step}", file=out)

    status = lr.install(progress=_progress)
    if status.state != lr.STATE_READY:
        print(f"  ⚠ {status.error or 'The install did not finish.'} {LATER_HINT}", file=out)
        return False
    print("  ✓ Answer check installed and verified", file=out)
    return True


def start_background_install() -> bool:
    """The install in its own session, logging next to the runtime: it
    outlives the command that started it. ``install()`` holds its own lock,
    so a dashboard setup started meanwhile is refused, not doubled."""
    from superlocalmemory.core import laya_runtime as lr

    try:
        run_dir = lr.runtime_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        with open(run_dir / LOG_FILE, "ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "superlocalmemory.cli.laya_offer"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True)
        return True
    except OSError:
        return False


def after_upgrade() -> str:
    """Called once, right after the upgrade banner. Never raises."""
    try:
        from superlocalmemory.cli.setup_wizard import is_interactive
        from superlocalmemory.core.config import SLMConfig

        return offer_after_upgrade(SLMConfig.load().retrieval, interactive=is_interactive())
    except Exception:
        return NOT_OFFERED


def main() -> int:
    from superlocalmemory.core import laya_runtime as lr

    status = lr.install(progress=lambda f, s: print(f"[{f * 100:5.1f}%] {s}", flush=True))
    print(f"answer check: {status.state} {status.error}".rstrip(), flush=True)
    return 0 if status.state == lr.STATE_READY else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["DECLINED", "FAILED", "INSTALLED", "NOT_OFFERED", "OPT_OUT_HINT", "STARTED",
           "after_upgrade", "declined", "forget_decline", "offer_after_upgrade", "opted_out",
           "remember_decline", "start_background_install", "supported",
           "wanted_after_upgrade"]
