"""Attribute shared daemon identity leaks to the test that caused them.

The session owns one starting identity, just as a daemon process does. Tests
that replace it must restore it. Compare after ALL fixture teardown so a
monkeypatch is accepted, and never include descriptor values (capabilities)
in diagnostics. Local apps created by a test are not shared state.
"""

import os
import sys
from copy import deepcopy

import pytest

_BEFORE = pytest.StashKey[dict]()
_MODULE = "superlocalmemory.server.unified_daemon"
_IDENTITY_ENV = ("SLM_DAEMON_PORT", "SLM_DAEMON_INSTANCE_ID", "SLM_DAEMON_CAPABILITY")


@pytest.fixture(scope="session", autouse=True)
def _shared_daemon_identity():
    from superlocalmemory.server import unified_daemon as daemon
    from superlocalmemory.server.routes.helpers import SLM_VERSION

    daemon._process_descriptor(daemon._configured_daemon_port(), SLM_VERSION, "starting")


def _snapshot():
    daemon = sys.modules.get(_MODULE)
    state = {"active": getattr(daemon, "_ACTIVE_DAEMON_DESCRIPTOR", None)}
    for name in ("app", "application"):
        app = getattr(daemon, name, None)
        state[name] = getattr(getattr(app, "state", None), "daemon_descriptor", None)
    state["environment"] = tuple(os.environ.get(name) for name in _IDENTITY_ENV)
    return deepcopy(state)


@pytest.fixture(autouse=True)
def _guard_shared_daemon_descriptor(request, _shared_daemon_identity):
    request.node.stash[_BEFORE] = _snapshot()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    try:
        result = yield
    finally:
        before = item.stash.get(_BEFORE, None)
        if before is not None:
            after = _snapshot()
            changed = [name for name in before if before[name] != after[name]]
            if changed:
                pytest.fail(
                    f"shared daemon descriptor leaked by {item.nodeid} "
                    f"({', '.join(changed)}); restore it with monkeypatch or fixture teardown",
                    pytrace=False,
                )
    return result
