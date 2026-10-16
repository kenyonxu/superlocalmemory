# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Confined backup IO: never follow a backup or manifest symlink."""

import os
import uuid
from contextlib import contextmanager

_WINDOWS = os.name == "nt"


def _windows_backend():
    from superlocalmemory.evolution import windows_backup_files

    return windows_backup_files


def checked(path, root):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Skill backup escapes its owned root or is a symlink")
    return path


@contextmanager
def directory(path, root, *, create=False):
    checked(path, root)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    checked(path, root)
    if _WINDOWS:
        with _windows_backend().locked_directory(path.absolute(), root) as _:
            yield None
        return
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def write(path, content, root):
    with directory(path, root, create=True) as parent:
        temporary = ".backup-" + uuid.uuid4().hex
        target = path.parent / temporary if parent is None else temporary
        options = {} if parent is None else {"dir_fd": parent}
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                     0o600, **options)
        try:
            with os.fdopen(fd, "wb") as stream:
                if parent is None:
                    from superlocalmemory.infra.owner_only_acl import restrict_to_owner

                    restrict_to_owner(target)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if parent is None:
                os.replace(target, path)
            else:
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(target, **options)
            except FileNotFoundError:
                pass


def read(path, root):
    with directory(path, root) as parent:
        if parent is None:
            return _windows_backend().read(path)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            return stream.read()
