"""Serialize OS service mutations across every data root of this user.

The lock is permanent: unlinking it would allow a second lock inode. Old
installers do not participate, so callers must still verify live definitions.
"""

from __future__ import annotations

import contextlib
import functools
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ParamSpec

from superlocalmemory.infra.mcp_registry_files import _owned
from superlocalmemory.infra.service_files import ServiceFiles

_P = ParamSpec("_P")
_HELD = threading.local()
_NAME = ".slm-service-mutation.lock"


def _lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def check_lock() -> None:
    files, fd = _HELD.lock
    _owned(os.fstat(fd))
    if files.fd is not None and not os.path.samestat(files.path.stat(), os.fstat(files.fd)):
        raise OSError("Service mutation lock directory was replaced")
    if not os.path.samestat(files._check(_NAME), os.fstat(fd)):
        raise OSError("Service mutation lock was replaced")


@contextlib.contextmanager
def mutation() -> Iterator[None]:
    if (getattr(_HELD, "lock", None) is not None
            and getattr(_HELD, "pid", None) == os.getpid()):
        check_lock()
        yield
        return
    with ServiceFiles(Path.home().resolve(), create=True) as files:
        files._check(_NAME, missing=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(files._target(_NAME), flags, 0o600, **files._kwargs())
        try:
            _owned(os.fstat(fd))
            if os.name == "nt":
                import msvcrt

                from superlocalmemory.infra.mcp_registry_files import _windows_owned
                from superlocalmemory.infra.owner_only_acl import restrict_to_owner

                restrict_to_owner(files.path / _NAME)
                _windows_owned(msvcrt.get_osfhandle(fd))
            _lock(fd)
            _HELD.lock = files, fd
            _HELD.pid = os.getpid()
            check_lock()
            yield
        finally:
            _HELD.lock = None
            os.close(fd)


def serialized(function: Callable[_P, bool]) -> Callable[_P, bool]:
    """Boolean service operations fail closed when another installer is active."""
    @functools.wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> bool:
        try:
            with mutation():
                return function(*args, **kwargs)
        except OSError:
            return False
    return wrapped
