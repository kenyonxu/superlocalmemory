"""Bounded, redacted diagnostics for the daemon child that just exited."""

from __future__ import annotations

import re
from pathlib import Path

from superlocalmemory.core.security_primitives import redact_secrets

_ERROR_LINE = re.compile(r"error|exception|failed|fatal|will not start", re.IGNORECASE)
_MAX_READ = 64 * 1024


def startup_error(log_file: Path, offset: int, exit_code: int) -> str:
    """Name the exit code and log, with the first error from this start attempt."""
    message = f"Daemon child exited with code {exit_code} before readiness. Check {log_file}."
    try:
        with log_file.open("rb") as stream:
            stream.seek(offset)
            text = stream.read(_MAX_READ).decode("utf-8", errors="replace")
        line = next((line.strip() for line in text.splitlines() if _ERROR_LINE.search(line)), "")
        if line:
            # Redact before truncating: a cut token can otherwise evade scanning.
            safe = redact_secrets(line, aggression="high")
            safe = "".join(c for c in safe if c.isprintable())
            message += f" First error: {safe[:500]}"
    except Exception:
        # A missing/unreadable log or unavailable sanitizer must not hide death.
        pass
    return message
