"""No-follow, owner-checked files for one canonical MCP registry directory."""

from __future__ import annotations

import contextlib
import os
import stat
import uuid
from pathlib import Path


def _owned(info: os.stat_result, *, directory: bool = False) -> None:
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or (not directory and info.st_nlink != 1):
        raise OSError("Unsafe MCP registry file type or link")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise OSError("MCP registry belongs to another account")
    if os.name != "nt" and directory and info.st_mode & 0o022:
        raise OSError("MCP registry is writable by another account")
    if getattr(info, "st_file_attributes", 0) & 0x400:
        raise OSError("MCP registry reparse points are not allowed")


def _windows_owned(handle: int) -> None:
    import win32api
    import win32con
    import win32security

    descriptor = win32security.GetSecurityInfo(
        handle, win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION
    )
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        if descriptor.GetSecurityDescriptorOwner() != owner:
            raise OSError("MCP registry belongs to another account")
    finally:
        token.Close()


def _open_root(root: Path, *, create: bool) -> int:
    """Traverse from the filesystem anchor using pinned, no-follow directories."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root.anchor, flags)
    try:
        for part in root.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def _windows_pin(path: Path):
    """Deny directory replacement while path-based Windows IO is in flight."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # Share read/write, but never delete; open the directory itself, not a reparse target.
    handle = create(str(path), 0x00020000, 3, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _owned(path.lstat(), directory=True)
        _windows_owned(handle)
        yield
    finally:
        close(handle)


class RegistryFiles:
    """Pin the directory so a later path swap cannot redirect any operation."""

    def __init__(self, root: Path, *, create: bool = False):
        self.root = Path(os.path.normcase(str(root.resolve())))
        self.path = self.root / "mcp-processes"
        self.create = create
        self.fd = None
        self._pins = contextlib.ExitStack()

    def __enter__(self):
        try:
            if os.name == "nt":
                if self.create:
                    self.root.mkdir(parents=True, exist_ok=True)
                self._pins.enter_context(_windows_pin(self.root))
                if self.create:
                    self.path.mkdir(mode=0o700, exist_ok=True)
                self._pins.enter_context(_windows_pin(self.path))
            else:
                root_fd = _open_root(self.root, create=self.create)
                try:
                    if self.create:
                        try:
                            os.mkdir("mcp-processes", mode=0o700, dir_fd=root_fd)
                        except FileExistsError:
                            pass
                    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                    self.fd = os.open("mcp-processes", flags, dir_fd=root_fd)
                    _owned(os.fstat(self.fd), directory=True)
                finally:
                    os.close(root_fd)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self._pins.close()

    def _target(self, name: str):
        if not name or Path(name).name != name:
            raise ValueError("Invalid MCP registry filename")
        return name if self.fd is not None else self.path / name

    def _kwargs(self):
        return {"dir_fd": self.fd} if self.fd is not None else {}

    def _stat(self, name: str):
        return os.stat(self._target(name), follow_symlinks=False, **self._kwargs())

    def _check(self, name: str, *, missing: bool = False):
        try:
            info = self._stat(name)
        except FileNotFoundError:
            if missing:
                return None
            raise
        _owned(info)
        return info

    def names(self) -> list[str]:
        return sorted(os.listdir(self.fd if self.fd is not None else self.path))

    def read(self, name: str) -> str:
        before = self._check(name)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(self._target(name), flags, **self._kwargs())
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            opened = os.fstat(stream.fileno())
            _owned(opened)
            if os.name == "nt":
                import msvcrt

                _windows_owned(msvcrt.get_osfhandle(stream.fileno()))
            if not os.path.samestat(before, opened):
                raise OSError("MCP registry record changed while opening")
            return stream.read()

    def write(self, name: str, content: str) -> None:
        self._check(name, missing=True)
        temporary = f".{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self._target(temporary), flags, 0o600, **self._kwargs())
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                if os.name == "nt":
                    from superlocalmemory.infra.owner_only_acl import restrict_to_owner

                    restrict_to_owner(self.path / temporary)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._check(name, missing=True)
            kwargs = {"src_dir_fd": self.fd, "dst_dir_fd": self.fd} if self.fd is not None else {}
            os.replace(self._target(temporary), self._target(name), **kwargs)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self._target(temporary), **self._kwargs())

    def remove(self, name: str) -> None:
        if self._check(name, missing=True) is not None:
            os.unlink(self._target(name), **self._kwargs())
