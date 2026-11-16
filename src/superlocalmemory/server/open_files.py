# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Give the daemon enough open files for SQLite, LanceDB and its sockets.

A daemon started by launchd on macOS gets a soft limit of 256 open files.
Under load LanceDB ran out ("Too many open files", os error 24) and the vector
copy of a new memory was parked until a restart (15 Oct 2026, 4.2.6). The soft
limit is raised once at start, never above the hard limit, and never lowered.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TARGET = 8192


def raise_open_file_limit(target: int = TARGET) -> tuple[int, int] | None:
    """Raise RLIMIT_NOFILE's soft limit toward ``target``; returns (before, after)."""
    try:
        import resource
    except ImportError:  # Windows has no RLIMIT_NOFILE; its handle limit is high
        return None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        wanted = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft != resource.RLIM_INFINITY and soft < wanted:
            resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))
            logger.info("Open-file limit raised from %d to %d", soft, wanted)
            return soft, wanted
        return soft, soft
    except (ValueError, OSError) as exc:  # a refusal must never stop the daemon
        logger.warning("Could not raise the open-file limit: %s", exc)
        return None
