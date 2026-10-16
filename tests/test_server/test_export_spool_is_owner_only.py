# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A spooled export holds a person's whole memory: every copy must be owner-only.

The plain temporary file came from mkstemp (0600), but the compressed copy was
created by gzip.open with the process umask (0644 under the usual 022), so on a
shared temp directory another local user could read the export while it was
being served.
"""

from __future__ import annotations

import os
import stat

from superlocalmemory.server.routes.export_delivery import spool_export


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_the_compressed_export_is_owner_only_even_with_an_open_umask() -> None:
    old = os.umask(0o000)
    try:
        spooled = spool_export(lambda fp: fp.write(b"x" * 4096) and 1, gzip_over=10)
    finally:
        os.umask(old)
    try:
        assert spooled.gzipped
        assert _mode(spooled.path) == 0o600
    finally:
        spooled.path.unlink(missing_ok=True)


def test_the_plain_export_is_owner_only() -> None:
    spooled = spool_export(lambda fp: fp.write(b"{}") and 1)
    try:
        assert not spooled.gzipped
        assert _mode(spooled.path) == 0o600
    finally:
        spooled.path.unlink(missing_ok=True)
