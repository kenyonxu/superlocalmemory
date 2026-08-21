# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Two things a memory must carry, and until 4.0.10 usually did not.

WHEN IT IS ABOUT. The internal admission record has had a ``session_date``
field all along; the HTTP model that feeds it did not, and every memory arrives
over HTTP -- command line, tool interface and dashboard alike. So a caller had
no way to say "this happened in March" and every memory was stamped with the
day it was written. Measured: 200 of the 200 most recent facts have an
observation_date and 196 of them are their own ingestion date.

WHICH SESSION IT BELONGS TO. ``recall`` has resolved this through a four-step
ladder since S9-DASH-10. ``remember`` had no ladder: it stored whatever it was
handed, which for a caller that does not pass one is nothing. Measured: 192 of
3,894 genuine facts carry a session_id (4.9%), 4 of the 200 most recent (2%).

The second is not bookkeeping. ``RetrievalEngine`` promotes results so the top
of an answer spans more than one session, and a fact with no session_id can
never be promoted by it -- so that mechanism was running against a corpus where
95% of rows were indistinguishable.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from superlocalmemory.mcp.session_binding import (
    SESSION_ENV_VARS,
    resolve_session_id,
)


class TestTheHttpModelAcceptsADate:
    def test_the_field_exists_and_defaults_to_empty(self) -> None:
        from superlocalmemory.server.unified_daemon import RememberRequest

        assert RememberRequest(content="x").session_date == "", (
            "the default must be empty, so an omitted date keeps the old "
            "behaviour of dating the memory today"
        )

    @pytest.mark.parametrize("value", [
        "2026-03-14",
        "2026-03-14T10:00:00+00:00",
        "2026-03-14T10:00:00Z",
    ])
    def test_it_accepts_a_date_and_a_timestamp(self, value: str) -> None:
        from superlocalmemory.server.unified_daemon import RememberRequest

        assert RememberRequest(content="x", session_date=value).session_date

    @pytest.mark.parametrize("value", [
        "last tuesday", "14/03/2026", "2026-13-45", "March 2026",
    ])
    def test_it_refuses_a_malformed_date_instead_of_ignoring_it(
        self, value: str,
    ) -> None:
        """Ignoring it would recreate the bug one layer up.

        A dropped date leaves the caller believing it was recorded while the
        memory quietly files itself under today. Rejecting is recoverable: the
        caller sees the error and resends.
        """
        import pydantic

        from superlocalmemory.server.unified_daemon import RememberRequest

        with pytest.raises(pydantic.ValidationError) as exc:
            RememberRequest(content="x", session_date=value)
        assert "YYYY-MM-DD" in str(exc.value), (
            "the error does not say what format is expected, so the caller "
            "cannot fix it from the message"
        )

    def test_the_handler_passes_it_to_the_admission_record(self) -> None:
        """A validated field nothing reads is decoration.

        The admission record already had the field; the gap was the handler
        never filling it, so that is what this asserts.
        """
        import inspect

        from superlocalmemory.server import unified_daemon

        src = inspect.getsource(unified_daemon)
        assert "session_date=req.session_date," in src, (
            "the remember handler builds its admission record without the "
            "date, so the field is accepted and then discarded"
        )

    def test_the_tool_forwards_it(self) -> None:
        import inspect

        from superlocalmemory.mcp import tools_core

        src = inspect.getsource(tools_core)
        assert '"session_date": session_date,' in src, (
            "the remember tool does not forward session_date to the daemon"
        )


class TestSessionResolutionIsSharedBetweenReadAndWrite:
    def test_an_explicit_id_always_wins(self) -> None:
        with patch.dict(os.environ, {"SLM_SESSION_ID": "from-env"}):
            assert resolve_session_id("explicit", agent_id="a") == "explicit"

    def test_the_environment_is_used_when_nothing_was_passed(self) -> None:
        with patch.dict(os.environ, {"SLM_SESSION_ID": "env-session"}):
            assert resolve_session_id("", agent_id="a") == "env-session"

    def test_slms_own_variable_beats_the_hosts(self) -> None:
        """A user must be able to override a host that sets an unhelpful value."""
        assert SESSION_ENV_VARS.index("SLM_SESSION_ID") < SESSION_ENV_VARS.index(
            "CLAUDE_SESSION_ID"
        )
        with patch.dict(os.environ, {
            "SLM_SESSION_ID": "ours", "CLAUDE_SESSION_ID": "theirs",
        }):
            assert resolve_session_id("", agent_id="a") == "ours"

    def test_whitespace_is_not_a_session(self) -> None:
        with patch.dict(os.environ, {"SLM_SESSION_ID": "   "}, clear=False):
            got = resolve_session_id("   ", agent_id="agent-7")
            assert got != "   "

    def test_the_write_path_refuses_the_synthetic_fallback(self) -> None:
        """`mcp:<agent>` is right for settling an outcome, wrong as a stored id.

        As a session_id it would file every memory an agent ever wrote under
        one session, and the engine's diversity promotion would then treat a
        whole history as a single conversation -- worse than the empty string
        it replaces.
        """
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "superlocalmemory.hooks.session_registry.lookup_by_parent",
                return_value="",
            ), patch(
                "superlocalmemory.hooks.session_registry.most_recent_active",
                return_value="",
            ):
                assert resolve_session_id(
                    "", agent_id="agent-7", allow_agent_fallback=False,
                ) == ""
                assert resolve_session_id(
                    "", agent_id="agent-7", allow_agent_fallback=True,
                ) == "mcp:agent-7"

    def test_a_broken_registry_does_not_break_the_call(self) -> None:
        """A session hint is a nicety. Losing a memory is not."""
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "superlocalmemory.hooks.session_registry.lookup_by_parent",
                side_effect=RuntimeError("registry on fire"),
            ):
                assert resolve_session_id(
                    "", agent_id="a", allow_agent_fallback=False,
                ) == ""

    def test_both_tools_call_the_shared_resolver(self) -> None:
        """Two copies of this ladder would drift, and did.

        recall had one and remember had none, which is the whole defect. A
        second private copy would reintroduce the same class of bug.
        """
        import inspect

        from superlocalmemory.mcp import tools_core

        # Assert on the two functions individually rather than counting
        # occurrences in the module. A count is the wrong instrument: the first
        # draft of this test expected three (two calls plus imports) and read
        # two, because `from ... import resolve_session_id` carries no paren.
        # It would also have passed with both calls inside one function.
        remember_src = inspect.getsource(
            _named_inner(tools_core.register_core_tools, "remember")
        )
        recall_src = inspect.getsource(
            _named_inner(tools_core.register_core_tools, "recall")
        )
        assert "resolve_session_id(" in remember_src, (
            "remember does not resolve a session id, so a caller that omits "
            "one still stores nothing"
        )
        assert "resolve_session_id(" in recall_src

        src = inspect.getsource(tools_core)
        assert 'os.environ.get("SLM_SESSION_ID")' not in src, (
            "an inline copy of the environment lookup is back in tools_core; "
            "it belongs in session_binding so both paths share it"
        )
        assert "allow_agent_fallback=False" in remember_src, (
            "the write path must refuse the synthetic per-agent id"
        )
        assert "allow_agent_fallback=True" in recall_src, (
            "the read path needs it: an outcome has to be settled against "
            "something"
        )


def _named_inner(outer: object, name: str) -> object:
    """Pull a closure-defined tool function out of its registrar.

    The MCP tools are defined inside register_core_tools and attached to the
    server by decorator, so they are reachable only through the registrar's
    code object constants. Worth the indirection: asserting per-function is
    what makes "both call it" mean both, rather than "the module mentions it
    twice".
    """
    import types

    for const in outer.__code__.co_consts:
        if isinstance(const, types.CodeType) and const.co_name == name:
            return types.FunctionType(const, outer.__globals__, name)
    raise AssertionError(f"no inner function named {name!r} in {outer.__name__}")
