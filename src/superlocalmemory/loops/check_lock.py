# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""One operator check at a time per data root, through receipt validation."""

import os
import stat
from contextlib import contextmanager
from pathlib import Path

from superlocalmemory.loops.private_workspace import directory_handle


@contextmanager
def exclusive_check(store):
    import fcntl  # Unsupported hosts are refused before taking a check lock.

    with directory_handle(store) as directory:
        fd = os.open('execution.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Check lock must be a regular file')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield Path(store)
    finally:
        os.close(fd)
