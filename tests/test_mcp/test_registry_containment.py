"""PR-245 #1: reconnect must never traverse another installation's files."""

import json
import os
import stat

import pytest

from superlocalmemory.infra import mcp_processes as registry


@pytest.fixture(autouse=True)
def synthetic_processes(monkeypatch):
    monkeypatch.setattr(registry, "process_identity", lambda pid: f"start-{pid}")
    monkeypatch.setattr("superlocalmemory.infra.mcp_legacy.discover", lambda *args: [])


def register(root, session="one"):
    registry.register(root, session, pid=101, host="codex", version="old", tools=[])


def test_directory_alias_cannot_reconnect_another_root(tmp_path):
    first, second = tmp_path / "A", tmp_path / "B"
    register(first)
    second.mkdir()
    (second / "mcp-processes").symlink_to(first / "mcp-processes", target_is_directory=True)
    assert registry.request_reconnect(second, "one") is False
    assert registry.reconnect_requested(first, "one") is False
    assert registry.snapshot(second)["count"] == 0
    registry.unregister(second, "one")
    assert (first / "mcp-processes" / "one.json").exists()
    with pytest.raises(OSError):
        register(second, "two")
    assert not (first / "mcp-processes" / "two.json").exists()


def test_flag_symlink_preserves_outside_bytes(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    register(root)
    outside.write_bytes(b"original outside bytes")
    (root / "mcp-processes" / "one.reconnect").symlink_to(outside)
    assert registry.request_reconnect(root, "one") is False
    assert outside.read_bytes() == b"original outside bytes"
    outside.write_text("start-101")
    assert registry.reconnect_requested(root, "one") is False
    registry.unregister(root, "one")
    assert outside.read_text() == "start-101"


def test_record_symlink_is_ignored_and_cannot_be_registered_over(tmp_path):
    root, other = tmp_path / "root", tmp_path / "other"
    register(other)
    directory = root / "mcp-processes"
    directory.mkdir(parents=True)
    outside = other / "mcp-processes" / "one.json"
    original = outside.read_bytes()
    (directory / "one.json").symlink_to(outside)
    assert registry.snapshot(root)["count"] == 0
    assert registry.request_reconnect(root, "one") is False
    with pytest.raises(OSError):
        register(root)
    registry.unregister(root, "one")
    assert outside.read_bytes() == original


def test_predictable_temporary_symlink_is_not_followed(tmp_path):
    directory = tmp_path / "root" / "mcp-processes"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep this")
    outside.chmod(0o644)
    (directory / "one.tmp").symlink_to(outside)
    register(directory.parent)
    assert outside.read_bytes() == b"keep this"
    assert stat.S_IMODE(outside.stat().st_mode) != 0o600
    assert registry.snapshot(directory.parent)["count"] == 1
    assert stat.S_IMODE((directory / "one.json").stat().st_mode) == 0o600


def test_copied_record_is_bound_to_canonical_root(tmp_path):
    first, second = tmp_path / "A", tmp_path / "B"
    register(first)
    directory = second / "mcp-processes"
    directory.mkdir(parents=True)
    (directory / "one.json").write_bytes((first / "mcp-processes" / "one.json").read_bytes())
    assert registry.snapshot(second)["count"] == 0
    assert registry.request_reconnect(second, "one") is False
    assert registry.reconnect_requested(first, "one") is False


def test_flag_hardlink_cannot_overwrite_outside_bytes(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    register(root)
    outside.write_bytes(b"keep this too")
    os.link(outside, root / "mcp-processes" / "one.reconnect")
    assert registry.request_reconnect(root, "one") is False
    assert outside.read_bytes() == b"keep this too"


def test_record_contains_canonical_root_and_marker_is_private(tmp_path):
    register(tmp_path)
    row = json.loads((tmp_path / "mcp-processes" / "one.json").read_text())
    assert row["data_root"] == os.path.normcase(str(tmp_path.resolve()))
    assert registry.request_reconnect(tmp_path, "one")
    assert stat.S_IMODE((tmp_path / "mcp-processes" / "one.reconnect").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd race; Windows pins deny rename")
def test_directory_swap_after_open_does_not_retarget_reconnect(tmp_path, monkeypatch):
    from superlocalmemory.infra.mcp_registry_files import RegistryFiles

    first, second = tmp_path / "A", tmp_path / "B"
    register(first)
    register(second)
    directory = second / "mcp-processes"
    original_read = RegistryFiles.read

    def swap_after_read(files, name):
        content = original_read(files, name)
        if name == "one.json":
            directory.rename(second / "original-registry")
            directory.symlink_to(first / "mcp-processes", target_is_directory=True)
        return content

    monkeypatch.setattr(RegistryFiles, "read", swap_after_read)
    assert registry.request_reconnect(second, "one")
    assert not (first / "mcp-processes" / "one.reconnect").exists()
    assert (second / "original-registry" / "one.reconnect").read_text() == "start-101"


def test_leaf_swap_during_publish_does_not_write_through_link(tmp_path, monkeypatch):
    from superlocalmemory.infra.mcp_registry_files import RegistryFiles

    register(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    flag = tmp_path / "root" / "mcp-processes" / "one.reconnect"
    original_check = RegistryFiles._check
    checks = []

    def plant_link(files, name, **kwargs):
        result = original_check(files, name, **kwargs)
        if name == "one.reconnect":
            checks.append(name)
            if len(checks) == 1:
                flag.symlink_to(outside)
        return result

    monkeypatch.setattr(RegistryFiles, "_check", plant_link)
    assert registry.request_reconnect(tmp_path / "root", "one") is False
    assert outside.read_bytes() == b"untouched"
    assert not list(flag.parent.glob(".*.tmp"))


def test_windows_synthetic_mode_bits_do_not_reject_owned_directory(monkeypatch):
    from types import SimpleNamespace

    from superlocalmemory.infra.mcp_registry_files import _owned

    info = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o777, st_uid=getattr(os, "getuid", lambda: 0)(), st_nlink=1
    )
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        _owned(info, directory=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX path traversal race")
def test_root_swap_during_directory_open_cannot_touch_other_install(tmp_path, monkeypatch):
    first, second = tmp_path / "A", tmp_path / "B"
    register(first)
    register(second)
    outside = second / "mcp-processes" / "one.json"
    original = outside.read_bytes()
    original_open = os.open
    swapped = []

    def swap_root(path, flags, *args, **kwargs):
        if not swapped and (str(path) == str(first / "mcp-processes") or str(path) == "A"):
            swapped.append(True)
            first.rename(tmp_path / "original-A")
            first.symlink_to(second, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_root)
    with pytest.raises(OSError):
        register(first)
    assert swapped
    assert outside.read_bytes() == original


def test_windows_pin_requests_permission_to_verify_owner(tmp_path, monkeypatch):
    import ctypes
    from types import SimpleNamespace

    from superlocalmemory.infra import mcp_registry_files as files

    calls = []

    def create(*args):
        calls.append(args)
        return 123

    def close(handle):
        return True

    kernel = SimpleNamespace(CreateFileW=create, CloseHandle=close)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(files, "_windows_owned", lambda handle: None)
    with files._windows_pin(tmp_path):
        assert calls[0][1] & 0x00020000  # READ_CONTROL for GetSecurityInfo
        assert calls[0][2] == 3  # deny FILE_SHARE_DELETE throughout the pin
