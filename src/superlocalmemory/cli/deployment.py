"""Non-secret deployment policy shared by CLI and operating-system services."""

from __future__ import annotations

import hashlib
import json
import os
import string

from superlocalmemory.infra.data_root import canonical_data_root, state_path


def configured_port(saved_port: int | None = None) -> int:
    """Resolve the explicit environment, saved configuration, then default."""
    raw = os.environ.get("SLM_DAEMON_PORT")
    if not raw and saved_port is not None:
        raw = saved_port
    if not raw:
        try:
            config = json.loads(state_path("config.json").read_text(encoding="utf-8"))
            raw = config.get("daemon_port")
        except (OSError, ValueError, AttributeError):
            raw = None
    try:
        port = int(raw or 8765)
    except (TypeError, ValueError):
        port = 8765
    return port if 1 <= port <= 65535 else 8765


def runtime_port() -> int:
    """Prefer the namespace's recorded endpoint without probing another port."""
    from superlocalmemory.infra.daemon_identity import read_descriptor

    descriptor = read_descriptor()
    return descriptor.port if descriptor is not None else configured_port()


def service_identity() -> str:
    """A service name cannot alias a different data root or listening port."""
    namespace = f"{canonical_data_root()}:{configured_port()}"
    suffix = hashlib.sha256(namespace.encode()).hexdigest()[:16]
    label = os.environ.get("SLM_SERVICE_LABEL", "com.qualixar.superlocalmemory")
    allowed = string.ascii_letters + string.digits + "._-"
    if not label or any(c not in allowed for c in label):
        raise ValueError("SLM_SERVICE_LABEL must contain only letters, numbers, '.', '_' or '-'")
    return f"{label}.{suffix}"


def security_environment() -> dict[str, str]:
    """Persist only non-secret enforcement flags; credentials stay in the store."""
    keys = ("SLM_REQUIRE_CREDENTIALS", "SLM_REQUIRE_API_KEY_LOOPBACK")
    policy = {
        key: "1" if (os.environ[key].strip().lower() in {"1", "true", "yes", "on"}
                     if key == "SLM_REQUIRE_API_KEY_LOOPBACK" else os.environ[key] == "1") else "0"
        for key in keys if key in os.environ
    }
    if "SLM_DISABLE_LEGACY_PORT" in os.environ:
        enabled = os.environ["SLM_DISABLE_LEGACY_PORT"].lower() in ("1", "true")
        policy["SLM_DISABLE_LEGACY_PORT"] = "1" if enabled else "0"
    return policy


def systemd_security_lines() -> str:
    return "\n".join(
        f'Environment="{key}={value}"' for key, value in security_environment().items()
    )


def windows_security_lines() -> str:
    return "".join(
        f'WshShell.Environment("Process")("{key}") = "{value}"\n'
        for key, value in security_environment().items()
    )
