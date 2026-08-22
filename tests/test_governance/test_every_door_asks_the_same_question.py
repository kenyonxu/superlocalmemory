"""A workspace that requires a login requires it at every door.

"Company mode" is two independent settings — one in a config file, one in the
workspace's own role settings that the dashboard toggle writes. Both mean the
same thing, so every entry point has to read both. Each time one of them was
found reading only the config file, the effect was the same: the toggle refused
a write on one transport and admitted the identical write on another.

The second half of this file is about what happens when a role cannot be read.
A lookup that failed is not a lookup that said yes.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture()
def login_required_store(tmp_path, monkeypatch):
    """A workspace whose config says personal and whose settings say otherwise."""
    import pathlib as _pathlib

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SLM_USER_SESSION", raising=False)

    from superlocalmemory.access.rbac import RbacEngine
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.migration_runner import apply_all

    config = SLMConfig.load()
    db = DatabaseManager(config.db_path)
    db.initialize(schema)
    db.close()
    apply_all(_pathlib.Path(state_path("learning.db")), _pathlib.Path(config.db_path))

    rbac = RbacEngine(str(config.db_path))
    rbac.set_require_login(True)
    return tmp_path


class TestEveryEntryPointReadsBothSettings:
    """Checked against the source of each gate, because the alternative is
    standing up four transports; the string being looked for is the name of the
    function that reads both, so this cannot pass by coincidence."""

    def _reads_both(self, function) -> bool:
        return "_company_mode_active" in inspect.getsource(function)

    def test_the_mcp_gate_reads_both(self):
        from superlocalmemory.core.admission import admits

        assert self._reads_both(admits)

    def test_the_command_line_gate_reads_both(self):
        from superlocalmemory.core.admission import gate_cli_mutation

        assert self._reads_both(gate_cli_mutation)

    def test_the_read_scope_clamp_reads_both(self):
        from superlocalmemory.core.admission import enforce_read_scope

        assert self._reads_both(enforce_read_scope)

    def test_the_network_write_gate_reads_both(self, login_required_store, monkeypatch):
        """This one was found reading the config file alone, after the other
        three had been fixed.

        Asked by running it. The config file says personal and the workspace
        settings say a login is required, so the decision this path asks for
        must be a company-mode decision. (The caller here is the machine owner,
        who is admitted either way — what is being checked is which question
        was asked, not the answer.)
        """
        from superlocalmemory.server.routes import memories

        asked: list[str] = []

        def _watch(kind, actor, *, mode):
            asked.append(mode)

        monkeypatch.setattr(memories, "_admit_http_mutation", memories._admit_http_mutation)
        import superlocalmemory.core.admission as admission

        monkeypatch.setattr(admission, "admit", _watch)
        memories._admit_http_mutation(_anonymous_request(), "delete")

        assert asked == ["company"], (
            f"the network write path asked for a {asked} decision on a "
            f"workspace that requires a login"
        )


def _anonymous_request():
    """A request from nobody in particular, with no app state."""
    class _Client:
        host = "127.0.0.1"

    class _Request:
        client = _Client()
        headers: dict = {}
        cookies: dict = {}

        class app:
            class state:
                deployment = None

    return _Request()


class TestReadingSomebodysMemoriesIsGuarded:
    """Reading is not a lesser operation than writing when what is read is
    somebody's memories."""

    _RETURN_MEMORY_CONTENT = ("recall", "search", "fetch", "list_recent", "session_init")

    def test_every_tool_that_returns_memories_is_gated(self):
        from superlocalmemory.core.admission import _REQUIRED_MCP_GATES

        missing = [
            name for name in self._RETURN_MEMORY_CONTENT
            if name not in _REQUIRED_MCP_GATES
        ]
        assert not missing, (
            f"{missing} return stored memories and are not on the list the "
            f"startup check enforces, so a workspace that requires a login "
            f"would hand their contents to an unauthenticated caller"
        )

    def test_registering_the_tools_actually_decorates_them(self):
        """The list above is worth something only if the tools carry the gate.

        The names are recorded when the decorator runs, which happens when the
        tools are registered on a server -- not on import. So this registers
        them on a stand-in server and reads what was recorded.
        """
        from superlocalmemory.core.admission import _GATED_MCP_TOOLS
        from superlocalmemory.mcp.tools_active import register_active_tools
        from superlocalmemory.mcp.tools_core import register_core_tools

        class _CollectingServer:
            def tool(self, *args, **kwargs):
                return lambda fn: fn

        server = _CollectingServer()
        register_core_tools(server, lambda: None)
        register_active_tools(server, lambda: None)

        for name in self._RETURN_MEMORY_CONTENT:
            assert name in _GATED_MCP_TOOLS, (
                f"{name} is declared as needing a gate and does not carry one"
            )

    def test_an_enterprise_store_refuses_to_start_with_a_gap(self):
        """The list is enforced, not decorative."""
        from superlocalmemory.core.admission import (
            _GATED_MCP_TOOLS,
            coverage_self_check,
        )
        from superlocalmemory.core.config import DEPLOYMENT_ENTERPRISE

        snapshot = set(_GATED_MCP_TOOLS)
        try:
            _GATED_MCP_TOOLS.discard("fetch")
            with pytest.raises(RuntimeError):
                coverage_self_check(DEPLOYMENT_ENTERPRISE)
        finally:
            _GATED_MCP_TOOLS.clear()
            _GATED_MCP_TOOLS.update(snapshot)


class TestAFailedRoleLookupIsNotAYes:
    def test_an_unreadable_role_answers_retry_rather_than_granting_one(self):
        """It used to return the least-privileged *write-capable* role, so any
        database error during the lookup promoted a viewer to someone who can
        write — at exactly the moment the store was under stress."""
        from fastapi import HTTPException

        from superlocalmemory.server import rbac_enforce

        class _Request:
            class app:
                class state:
                    pass

        class _Failing:
            def get_role(self, *args, **kwargs):
                raise RuntimeError("the roles table is unreadable right now")

        original_principal = rbac_enforce.resolve_principal
        original_engine = rbac_enforce.get_rbac_engine
        rbac_enforce.resolve_principal = lambda request: {
            "kind": "user", "user_id": "someone",
        }
        rbac_enforce.get_rbac_engine = lambda state: _Failing()
        try:
            with pytest.raises(HTTPException) as raised:
                rbac_enforce.resolve_actor_roles(_Request())
            assert raised.value.status_code == 503
        finally:
            rbac_enforce.resolve_principal = original_principal
            rbac_enforce.get_rbac_engine = original_engine

    def test_the_machine_owner_is_still_the_owner(self):
        """The control: failing closed must not lock out the person whose
        machine this is."""
        from superlocalmemory.core.actor_context import ActorRole
        from superlocalmemory.server import rbac_enforce

        class _Request:
            class app:
                class state:
                    pass

        original = rbac_enforce.resolve_principal
        rbac_enforce.resolve_principal = lambda request: {"kind": "owner"}
        try:
            assert rbac_enforce.resolve_actor_roles(_Request()) == frozenset(
                {ActorRole.OWNER}
            )
        finally:
            rbac_enforce.resolve_principal = original


class TestWritingThroughAnySideDoorIsChecked:
    def test_the_bulk_ingest_route_checks_the_caller_may_write(self):
        from superlocalmemory.server.routes import ingest

        source = inspect.getsource(ingest)
        assert "require_permission" in source and "Permission.WRITE" in source, (
            "the bulk ingest route establishes who is calling and never asks "
            "whether they may write, so a read-only user can write through it"
        )

    @pytest.mark.parametrize("route,call", [
        ("create_retention_policy", lambda fn, r: fn(r, {"name": "x"})),
        ("delete_retention_policy", lambda fn, r: fn(r, "x")),
        ("enforce_retention", lambda fn, r: fn(r)),
        ("compliance_status", lambda fn, r: fn(r)),
    ])
    def test_a_caller_from_nowhere_cannot_touch_a_retention_rule(self, route, call):
        """Called, not read. A retention rule decides what is kept and for how
        long; these four had no gate at all."""
        import asyncio

        from fastapi import HTTPException

        from superlocalmemory.server.routes import compliance

        handler = getattr(compliance, route)
        with pytest.raises((HTTPException, PermissionError)):
            asyncio.run(call(handler, _remote_request()))


def _remote_request():
    """A request that did not come from this machine."""
    class _Client:
        host = "203.0.113.7"

    class _Request:
        client = _Client()
        headers: dict = {}

        class app:
            class state:
                daemon_descriptor = None

    return _Request()


class TestARefusalIsNeverRetriedLocally:
    def test_the_command_dispatcher_answers_a_refusal(self, monkeypatch, capsys):
        """Driven, not read. A refused command must exit, not raise."""
        from argparse import Namespace

        from superlocalmemory.cli import commands
        from superlocalmemory.cli.daemon import DaemonRefused

        def _refused(_args):
            raise DaemonRefused(401, "/remember")

        monkeypatch.setitem(
            commands.dispatch.__globals__.setdefault("_TEST_HANDLERS", {}),
            "noop", _refused,
        )
        # dispatch builds its own table, so exercise the same wrapper by
        # calling it with a command whose handler we replace on the module.
        monkeypatch.setattr(commands, "cmd_status", _refused, raising=False)
        with pytest.raises(SystemExit) as raised:
            commands.dispatch(Namespace(command="status"))
        assert raised.value.code == 1
        assert "authentication" in capsys.readouterr().out.lower()

    def test_a_refusal_is_a_distinct_outcome_from_an_outage(self):
        import urllib.error

        from superlocalmemory.cli.daemon import DaemonRefused

        assert issubclass(DaemonRefused, RuntimeError)
        refusal = DaemonRefused(401, "/remember")
        assert refusal.status == 401
        assert "401" in str(refusal)
        assert not isinstance(refusal, urllib.error.HTTPError)
