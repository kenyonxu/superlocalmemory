"""The descriptor guard attributes leaks after fixture restoration, without secrets."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("target", ["active", "app", "in_place"])
def test_guard_names_the_leaking_test_without_printing_identity(tmp_path, target):
    source = '''
from types import SimpleNamespace
from superlocalmemory.server import unified_daemon as daemon

def test_leaking_descriptor():
    descriptor = SimpleNamespace(port=0, capability="DO-NOT-PRINT-CAPABILITY")
    if TARGET == "active":
        daemon._ACTIVE_DAEMON_DESCRIPTOR = descriptor
    else:
        daemon.app = SimpleNamespace(state=SimpleNamespace(daemon_descriptor=descriptor))
'''.replace("TARGET", repr(target))
    if target == "in_place":
        source = source.replace(
            'def test_leaking_descriptor():',
            'daemon.app = SimpleNamespace(state=SimpleNamespace('
            'daemon_descriptor=SimpleNamespace(port=12345)))\n'
            'def test_leaking_descriptor():',
        ).replace(
            'daemon.app = SimpleNamespace(state=SimpleNamespace(daemon_descriptor=descriptor))',
            'daemon.app.state.daemon_descriptor.port = 0',
        )
    result = _run_guard(tmp_path, source)
    assert result.returncode == 1, result.stdout
    assert "shared daemon descriptor leaked by" in result.stdout
    assert "test_descriptor.py::test_leaking_descriptor" in result.stdout
    assert "DO-NOT-PRINT-CAPABILITY" not in result.stdout


def test_guard_accepts_monkeypatch_restoration_after_fixture_teardown(tmp_path):
    result = _run_guard(tmp_path, '''
from types import SimpleNamespace
from superlocalmemory.server import unified_daemon as daemon

def test_restored_descriptor(monkeypatch):
    monkeypatch.setattr(daemon, "_ACTIVE_DAEMON_DESCRIPTOR", SimpleNamespace(port=0))
    monkeypatch.setattr(daemon, "app", SimpleNamespace(state=SimpleNamespace(
        daemon_descriptor=SimpleNamespace(port=0))), raising=False)
''')
    assert result.returncode == 0, result.stdout
    assert "1 passed" in result.stdout


def test_guard_checks_even_when_another_fixture_teardown_fails(tmp_path):
    result = _run_guard(tmp_path, '''
import pytest
from types import SimpleNamespace
from superlocalmemory.server import unified_daemon as daemon

@pytest.fixture
def broken_teardown():
    yield
    daemon._ACTIVE_DAEMON_DESCRIPTOR = SimpleNamespace(port=0)
    raise RuntimeError("synthetic teardown failure")

def test_broken_teardown(broken_teardown):
    pass
''')
    assert result.returncode == 1, result.stdout
    assert "shared daemon descriptor leaked by" in result.stdout
    assert "test_descriptor.py::test_broken_teardown" in result.stdout


def _run_guard(tmp_path, source):
    (tmp_path / "test_descriptor.py").write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "test_descriptor.py"),
         "--noconftest", "-o", "addopts=", "-p", "tests._isolation_plugin",
         "-p", "tests.daemon_descriptor_guard", "-p", "no:cacheprovider",
         "--basetemp", str(tmp_path / "pytest-tmp"), "--tb=short", "-q"],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True, text=True, timeout=60,
    )
