"""PR-245 #2: old service names remain managed, scoped by recorded root."""

import plistlib
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from superlocalmemory.cli import service_installer as service

LEGACY = {"macos": "com.qualixar.superlocalmemory", "linux": "superlocalmemory",
          "windows": "SuperLocalMemory"}


def task_xml(path):
    task = ET.Element("Task", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
    action = ET.SubElement(ET.SubElement(task, "Actions"), "Exec")
    ET.SubElement(action, "Command").text = "wscript.exe"
    ET.SubElement(action, "Arguments").text = f'"{path}"'
    return ET.tostring(task, encoding="unicode")


def _systemd_show(home, live, argv):
    name = argv[argv.index("show") + 1]
    path = home / ".config" / "systemd" / "user" / f"{name}.service"
    if "--property=Environment" in argv:
        env, command = [], ""
        for line in live.get(name, "").splitlines():
            if line.startswith("Environment="):
                env.extend(shlex.split(line.split("=", 1)[1]))
            if line.startswith("ExecStart="):
                command = line.split("=", 1)[1]
        output = (f"Environment={shlex.join(env)}\n"
                  f"ExecStart={{ path={shlex.split(command)[0]} ; argv[]={command} ; "
                  "ignore_errors=no ; pid=123 ; status=0 }\n")
        return SimpleNamespace(returncode=0, stdout=output, stderr="")
    return SimpleNamespace(returncode=0, stdout=f"FragmentPath={path}\nDropInPaths=\n",
                           stderr="")


def _manager_run(home, calls, tasks, live, argv):
    calls.append(argv)
    if argv[0] == "launchctl" and "list" in argv:
        name = argv[-1]
        return SimpleNamespace(returncode=0 if name in live else 3,
                               stdout=live.get(name, ""), stderr="")
    if argv[0] == "launchctl" and argv[1] == "load":
        content = Path(argv[-1]).read_text()
        live[plistlib.loads(content.encode())["Label"]] = content
    if argv[0] == "launchctl" and argv[1] == "unload":
        live.pop(Path(argv[-1]).stem, None)
    if argv[0] == "systemctl" and "start" in argv:
        name = argv[-1]
        path = home / ".config" / "systemd" / "user" / f"{name}.service"
        live[name] = path.read_text()
    if argv[0] == "systemctl" and "show" in argv:
        return _systemd_show(home, live, argv)
    if argv[0] == "schtasks" and argv[1] == "/Query":
        name = argv[argv.index("/TN") + 1]
        return SimpleNamespace(returncode=0 if name in tasks else 1,
                               stdout=tasks.get(name, ""), stderr="")
    if argv[0] == "schtasks" and argv[1] == "/Delete":
        tasks.pop(argv[argv.index("/TN") + 1], None)
    if argv[0] == "schtasks" and argv[1] == "/Create":
        name = argv[argv.index("/TN") + 1]
        wrapper = argv[argv.index("/TR") + 1].split('"')[1]
        tasks[name] = task_xml(wrapper)
    return SimpleNamespace(returncode=0, stdout="active", stderr="")


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    home, root = tmp_path / "home", tmp_path / "root with spaces"
    home.mkdir()
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("SLM_DATA_DIR", str(root))
    monkeypatch.setenv("SLM_DAEMON_PORT", "19291")
    monkeypatch.delenv("SLM_SERVICE_LABEL", raising=False)
    calls, tasks, live = [], {}, {}
    monkeypatch.setattr(service.subprocess, "run",
                        lambda argv, **kwargs: _manager_run(home, calls, tasks, live, argv))
    return SimpleNamespace(home=home, root=root, calls=calls, tasks=tasks, live=live)


def write_definition(sandbox, platform, *, legacy=True):
    name = LEGACY[platform] if legacy else service.service_identity()
    if platform == "macos":
        path = sandbox.home / "Library" / "LaunchAgents" / f"{name}.plist"
        payload = plistlib.loads(service._macos_plist_content().encode())
        payload["Label"] = name
        content = plistlib.dumps(payload).decode()
    elif platform == "linux":
        path = sandbox.home / ".config" / "systemd" / "user" / f"{name}.service"
        content = service._linux_service_content()
    else:
        path = sandbox.root / ("start-daemon.vbs" if legacy else f"start-daemon-{name}.vbs")
        content = service._windows_vbs_content()
        sandbox.tasks[name] = task_xml(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    sandbox.live[name] = content
    sandbox.calls.clear()
    return name, path


def removed(sandbox, platform, name, path):
    if platform == "macos":
        return ["launchctl", "unload", str(path)] in sandbox.calls
    if platform == "linux":
        return all(["systemctl", "--user", action, name] in sandbox.calls
                   for action in ("stop", "disable"))
    return ["schtasks", "/Delete", "/TN", name, "/F"] in sandbox.calls


def test_exact_review_counterexample_uninstalls_old_macos_plist(sandbox):
    name, path = write_definition(sandbox, "macos")
    assert service.uninstall_macos()
    assert not path.exists()
    assert removed(sandbox, "macos", name, path)


@pytest.mark.parametrize("platform", LEGACY)
@pytest.mark.parametrize("changed_port", [False, True])
def test_status_finds_legacy_service_even_after_port_change(
    sandbox, monkeypatch, platform, changed_port,
):
    name, path = write_definition(sandbox, platform)
    original = path.read_bytes()
    if changed_port:
        monkeypatch.setenv("SLM_DAEMON_PORT", "19292")
    result = getattr(service, f"status_{platform}")()
    assert result["installed"]
    assert name in result["service_names"]
    assert path.read_bytes() == original
    assert not removed(sandbox, platform, name, path)


@pytest.mark.parametrize("platform", LEGACY)
@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_mutation_removes_owned_legacy_before_current_install(
    sandbox, monkeypatch, platform, operation,
):
    name, path = write_definition(sandbox, platform)
    monkeypatch.setenv("SLM_DAEMON_PORT", "19292")
    assert getattr(service, f"{operation}_{platform}")()
    assert not path.exists()
    assert removed(sandbox, platform, name, path)
    if operation == "install":
        result = getattr(service, f"status_{platform}")()
        assert service.service_identity() in result["service_names"]
        assert name not in result["service_names"]


@pytest.mark.parametrize("platform", LEGACY)
@pytest.mark.parametrize("operation", ["status", "install", "uninstall"])
def test_foreign_legacy_service_is_preserved(sandbox, monkeypatch, platform, operation):
    name, path = write_definition(sandbox, platform)
    original = path.read_bytes()
    foreign = sandbox.root / "other-install"
    foreign.mkdir()
    monkeypatch.setenv("SLM_DATA_DIR", str(foreign))
    result = getattr(service, f"{operation}_{platform}")()
    assert path.read_bytes() == original
    assert not removed(sandbox, platform, name, path)
    if operation == "status":
        assert result["installed"] is False
    else:
        assert result


@pytest.mark.parametrize("platform", LEGACY)
@pytest.mark.parametrize("operation", ["status", "install", "uninstall"])
def test_previous_namespaced_port_remains_managed(sandbox, monkeypatch, platform, operation):
    name, path = write_definition(sandbox, platform, legacy=False)
    monkeypatch.setenv("SLM_DAEMON_PORT", "19292")
    result = getattr(service, f"{operation}_{platform}")()
    if operation == "status":
        assert result["installed"]
        assert name in result["service_names"]
    else:
        assert result
        assert not path.exists()
        assert removed(sandbox, platform, name, path)


@pytest.mark.parametrize("platform", LEGACY)
@pytest.mark.parametrize("damage", ["missing-root", "wrong-port", "malformed"])
def test_unprovable_legacy_definitions_are_never_adopted(sandbox, platform, damage):
    name, path = write_definition(sandbox, platform)
    content = path.read_text()
    if damage == "missing-root":
        content = content.replace("SLM_DATA_DIR", "UNKNOWN_DATA_ROOT")
    elif damage == "wrong-port":
        content = content.replace("--port=19291", "--port=19299")
    else:
        content = "malformed definition"
    path.write_text(content)
    assert getattr(service, f"status_{platform}")()["installed"] is False
    assert getattr(service, f"uninstall_{platform}")()
    assert path.read_text() == content
    assert not removed(sandbox, platform, name, path)


@pytest.mark.parametrize("platform", LEGACY)
@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_os_removal_failure_preserves_legacy_and_reports_failure(
    sandbox, monkeypatch, platform, operation,
):
    name, path = write_definition(sandbox, platform)
    content = path.read_bytes()
    original_run = service.subprocess.run
    failing = {"macos": "unload", "linux": "stop", "windows": "/Delete"}[platform]

    def fail_removal(argv, **kwargs):
        if failing in argv:
            sandbox.calls.append(argv)
            return SimpleNamespace(returncode=2, stdout="", stderr="synthetic failure")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(service.subprocess, "run", fail_removal)
    assert getattr(service, f"{operation}_{platform}")() is False
    assert path.read_bytes() == content
    if platform == "windows":
        assert name in sandbox.tasks
    assert not any("load" in call or "start" in call or "/Create" in call
                   for call in sandbox.calls)


@pytest.mark.parametrize("platform", LEGACY)
def test_foreign_definition_at_current_identity_is_never_overwritten(sandbox, platform):
    name, path = write_definition(sandbox, platform, legacy=False)
    content = path.read_text().replace(str(sandbox.root), str(sandbox.root / "foreign"))
    path.write_text(content)
    assert getattr(service, f"install_{platform}")() is False
    assert path.read_text() == content
    assert not removed(sandbox, platform, name, path)


def test_windows_task_action_must_point_to_selected_root(sandbox):
    name, path = write_definition(sandbox, "windows")
    sandbox.tasks[name] = task_xml(sandbox.root / "foreign" / "start-daemon.vbs")
    assert service.status_windows()["installed"] is False
    assert service.uninstall_windows()
    assert path.exists()
    assert not removed(sandbox, "windows", name, path)


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_symlinked_legacy_definition_preserves_target(sandbox, platform):
    name, path = write_definition(sandbox, platform)
    outside = sandbox.root / "outside-definition"
    path.rename(outside)
    content = outside.read_bytes()
    path.symlink_to(outside)
    assert getattr(service, f"status_{platform}")()["installed"] is False
    assert getattr(service, f"uninstall_{platform}")()
    assert path.is_symlink()
    assert outside.read_bytes() == content
    assert not removed(sandbox, platform, name, path)


@pytest.mark.parametrize("active", [True, False])
def test_linux_status_preserves_service_activity(sandbox, monkeypatch, active):
    write_definition(sandbox, "linux")
    original_run = service.subprocess.run

    def activity(argv, **kwargs):
        if "is-active" in argv:
            sandbox.calls.append(argv)
            return SimpleNamespace(returncode=0 if active else 3,
                                   stdout="active" if active else "inactive", stderr="")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(service.subprocess, "run", activity)
    result = service.status_linux()
    assert result["installed"]
    assert result["active"] is active
    assert result["details"]


def test_macos_program_override_is_not_owned(sandbox):
    name, path = write_definition(sandbox, "macos")
    payload = plistlib.loads(path.read_bytes())
    payload["Program"] = "/usr/bin/foreign-launcher"
    path.write_bytes(plistlib.dumps(payload))
    assert service.status_macos()["installed"] is False
    assert service.uninstall_macos()
    assert path.exists()
    assert not removed(sandbox, "macos", name, path)


@pytest.mark.parametrize(
    "override", ["UnsetEnvironment=SLM_DATA_DIR", "PassEnvironment=SLM_DATA_DIR"]
)
def test_linux_environment_overrides_are_not_owned(sandbox, override):
    name, path = write_definition(sandbox, "linux")
    path.write_text(path.read_text().replace("Type=simple", "Type=simple\n" + override))
    assert service.status_linux()["installed"] is False
    assert service.uninstall_linux()
    assert path.exists()
    assert not removed(sandbox, "linux", name, path)


def test_linux_dropin_overrides_are_not_owned(sandbox):
    name, path = write_definition(sandbox, "linux")
    dropins = path.parent / f"{name}.service.d"
    dropins.mkdir()
    (dropins / "foreign.conf").write_text('[Service]\nEnvironment="SLM_DATA_DIR=/foreign"\n')
    assert service.status_linux()["installed"] is False
    assert service.uninstall_linux()
    assert path.exists()
    assert not removed(sandbox, "linux", name, path)


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_redirected_service_directory_cannot_receive_new_install(sandbox, platform):
    path = service._macos_plist_path() if platform == "macos" else service._linux_service_path()
    outside = sandbox.root / "foreign-services"
    outside.mkdir()
    path.parent.parent.mkdir(parents=True, exist_ok=True)
    path.parent.symlink_to(outside, target_is_directory=True)
    assert getattr(service, f"install_{platform}")() is False
    assert not list(outside.iterdir())
    assert not sandbox.calls


@pytest.mark.parametrize("property_value", ["FragmentPath=/foreign/unit.service\nDropInPaths=\n",
                                           "FragmentPath={path}\nDropInPaths=/foreign/override.conf\n"])
def test_linux_effective_definition_must_match_owned_file(sandbox, monkeypatch, property_value):
    name, path = write_definition(sandbox, "linux")
    original_run = service.subprocess.run

    def override(argv, **kwargs):
        if "show" in argv:
            sandbox.calls.append(argv)
            return SimpleNamespace(returncode=0, stdout=property_value.format(path=path), stderr="")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(service.subprocess, "run", override)
    assert service.status_linux()["installed"] is False
    assert service.uninstall_linux()
    assert path.exists()
    assert not removed(sandbox, "linux", name, path)


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_service_directory_writable_by_other_accounts_is_refused(sandbox, platform):
    import os

    if os.name == "nt":
        pytest.skip("Windows directory ownership is verified through handle security")
    path = service._macos_plist_path() if platform == "macos" else service._linux_service_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o777)
    assert getattr(service, f"install_{platform}")() is False
    assert not path.exists()


def test_windows_install_can_retry_after_task_creation_failure(sandbox, monkeypatch):
    original_run = service.subprocess.run

    def fail_create(argv, **kwargs):
        if "/Create" in argv:
            return SimpleNamespace(returncode=2, stdout="", stderr="synthetic failure")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(service.subprocess, "run", fail_create)
    assert service.install_windows() is False
    monkeypatch.setattr(service.subprocess, "run", original_run)
    assert service.install_windows()
    assert service.status_windows()["installed"]


def test_windows_missing_service_manager_returns_failure(sandbox, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("synthetic missing schtasks")

    monkeypatch.setattr(service.subprocess, "run", unavailable)
    assert service.install_windows() is False


def test_windows_environment_must_be_proven_at_launch(sandbox):
    name, path = write_definition(sandbox, "windows")
    content = path.read_text().replace(str(sandbox.root), str(sandbox.root / "foreign"))
    content += f'WshShell.Environment("Process")("SLM_DATA_DIR") = "{sandbox.root}"\n'
    path.write_text(content)
    assert service.status_windows()["installed"] is False
    assert service.uninstall_windows()
    assert path.read_text() == content
    assert not removed(sandbox, "windows", name, path)


def test_windows_foreign_launcher_basename_does_not_prove_ownership(sandbox):
    name, path = write_definition(sandbox, "windows")
    sandbox.tasks[name] = task_xml(path).replace("wscript.exe", r"C:\foreign\wscript.exe")
    assert service.status_windows()["installed"] is False
    assert service.uninstall_windows()
    assert path.exists()
    assert not removed(sandbox, "windows", name, path)


@pytest.mark.parametrize("override", ["RootDirectory=/foreign", "ExecStartPre=/foreign/launcher"])
def test_linux_execution_namespace_overrides_are_not_owned(sandbox, override):
    name, path = write_definition(sandbox, "linux")
    path.write_text(path.read_text().replace("Type=simple", "Type=simple\n" + override))
    assert service.status_linux()["installed"] is False
    assert service.uninstall_linux()
    assert path.exists()
    assert not removed(sandbox, "linux", name, path)
