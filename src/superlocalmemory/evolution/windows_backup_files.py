# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Windows handles reject reparse points and prevent directory substitution."""

from contextlib import contextmanager


def _api():
    import win32con
    import win32file

    return win32file, win32con


def _open(path, access):
    files, flags = _api()
    handle = files.CreateFile(
        str(path), access, flags.FILE_SHARE_READ | flags.FILE_SHARE_WRITE,
        None, flags.OPEN_EXISTING,
        getattr(flags, "FILE_FLAG_OPEN_REPARSE_POINT", 0x00200000)
        | flags.FILE_FLAG_BACKUP_SEMANTICS, None,
    )
    try:
        if files.GetFileInformationByHandle(handle)[0] & flags.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Skill backup contains a Windows reparse point")
    except BaseException:
        handle.Close()
        raise
    return handle


@contextmanager
def locked_directory(path, root):
    # Denying FILE_SHARE_DELETE on every owned ancestor prevents replacement
    # while absolute-path file operations execute within this context.
    root = root.resolve()
    relative = path.parent.relative_to(root)
    handles, current = [], root
    try:
        handles.append(_open(current, 0))
        for part in relative.parts:
            current /= part
            handles.append(_open(current, 0))
        yield
    finally:
        for handle in reversed(handles):
            handle.Close()


def read(path):
    files, flags = _api()
    handle = _open(path, flags.GENERIC_READ)
    try:
        size = files.GetFileSize(handle)
        return files.ReadFile(handle, size)[1] if size else b""
    finally:
        handle.Close()
