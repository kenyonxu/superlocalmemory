"""Windows dispatch must not require POSIX directory-relative IO."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from superlocalmemory.evolution import backup_files


def test_windows_backup_roundtrip_uses_platform_boundary(tmp_path, monkeypatch):
    calls = []

    @contextmanager
    def locked(path, root):
        calls.append(path)
        yield

    backend = SimpleNamespace(locked_directory=locked, read=lambda path: path.read_bytes())
    monkeypatch.setattr(backup_files, "_WINDOWS", True, raising=False)
    monkeypatch.setattr(backup_files, "_windows_backend", lambda: backend, raising=False)
    monkeypatch.delattr(backup_files.os, "O_DIRECTORY")
    path = tmp_path / "review" / "SKILL.md.bak"
    backup_files.write(path, b"original", tmp_path)
    assert backup_files.read(path, tmp_path) == b"original"
    assert calls == [path, path]


@pytest.mark.parametrize("reparse", [False, True])
def test_windows_handles_disallow_delete_sharing_and_reparse(tmp_path, monkeypatch, reparse):
    from superlocalmemory.evolution import windows_backup_files as windows

    calls, closed = [], []
    flags = SimpleNamespace(
        FILE_SHARE_READ=1, FILE_SHARE_WRITE=2, OPEN_EXISTING=3,
        FILE_FLAG_BACKUP_SEMANTICS=0x2000000,
        FILE_ATTRIBUTE_REPARSE_POINT=0x400,
    )

    def create(*args):
        calls.append(args)
        return SimpleNamespace(Close=lambda: closed.append(True))

    api = SimpleNamespace(
        CreateFile=create, GetFileInformationByHandle=lambda _: (0x400 if reparse else 0,),
    )
    monkeypatch.setattr(windows, "_api", lambda: (api, flags))
    if reparse:
        with pytest.raises(ValueError, match="reparse"):
            windows._open(tmp_path, 0)
    else:
        windows._open(tmp_path, 0).Close()
    assert closed == [True]
    assert calls[0][2] == 3  # read/write sharing; never FILE_SHARE_DELETE (4)
    assert calls[0][5] == 0x200000 | flags.FILE_FLAG_BACKUP_SEMANTICS
