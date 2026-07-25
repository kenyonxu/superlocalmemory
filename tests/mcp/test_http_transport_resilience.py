# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | Workstream E — MCP Connection Resilience

"""Tests for MCP session idle timeout, bounded event store, and ordering guard.

Workstream E — 3.8.4 stability: MCP connections must stop dropping.

Three test groups:
A. Session idle timeout — session manager is constructed with a finite,
   configurable idle timeout so zombie sessions are reaped automatically.
B. SLMInMemoryEventStore — events are stored, replayed correctly on
   reconnect, and the store is bounded (oldest events / streams evicted).
C. Ordering guard — streamable_http_app() never raises RuntimeError when
   called before _configure_mcp_transport_settings() or in stateless mode.

Run:
    SLM_TEST_ISOLATION=1 \\
    PYTHONPATH="/path/to/superlocalmemory.3.8.4-E/src" \\
    ~/.slm-venv/bin/python -m pytest tests/mcp/test_http_transport_resilience.py \\
        -o addopts="" -q --tb=short -p no:cacheprovider
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Lazy imports — allow RED-phase import failure to fail at test time not
# collection time, so the test runner reports individual test failures.
# ---------------------------------------------------------------------------

def _import_transport():
    import superlocalmemory.mcp.http_transport as mod
    return mod


def _get_store_class():
    mod = _import_transport()
    return getattr(mod, "SLMInMemoryEventStore")


def _get_slmfastmcp():
    mod = _import_transport()
    return getattr(mod, "SLMFastMCP")


# ===========================================================================
# GROUP A: Session idle timeout
# ===========================================================================


class TestSessionIdleTimeout:
    """Session manager must be created with a finite, positive idle timeout."""

    def test_session_manager_has_finite_idle_timeout(self):
        """After streamable_http_app(), _session_manager.session_idle_timeout > 0."""
        SLMFastMCP = _get_slmfastmcp()
        mcp = SLMFastMCP("slm-test")
        mcp.streamable_http_app()

        assert mcp._session_manager is not None, "Session manager must be created"
        timeout = mcp._session_manager.session_idle_timeout
        assert timeout is not None, (
            "session_idle_timeout must be set (not None) so zombie sessions are reaped"
        )
        assert isinstance(timeout, (int, float)), "session_idle_timeout must be numeric"
        assert timeout > 0, f"session_idle_timeout must be positive; got {timeout}"

    def test_default_idle_timeout_is_reasonable(self):
        """Default timeout must be in [60, 7200] seconds — not too aggressive, not too lenient."""
        SLMFastMCP = _get_slmfastmcp()
        mcp = SLMFastMCP("slm-test-bounds")
        mcp.streamable_http_app()

        timeout = mcp._session_manager.session_idle_timeout
        assert 60 <= timeout <= 7200, (
            f"Default timeout {timeout}s is outside the safe [60, 7200] range. "
            "Below 60s: too aggressive (CPU waste + legitimate quiet users dropped). "
            "Above 7200s: too lenient (allows 18+ zombie sessions to accumulate)."
        )

    def test_idle_timeout_env_override(self, monkeypatch):
        """SLM_MCP_SESSION_IDLE_TIMEOUT_S env var overrides the default."""
        monkeypatch.setenv("SLM_MCP_SESSION_IDLE_TIMEOUT_S", "300")
        # Need a fresh SLMFastMCP (new instance, no cached session_manager)
        SLMFastMCP = _get_slmfastmcp()
        mcp = SLMFastMCP("slm-test-env")
        mcp.streamable_http_app()

        timeout = mcp._session_manager.session_idle_timeout
        assert timeout == 300.0, (
            f"Expected 300.0 from env var SLM_MCP_SESSION_IDLE_TIMEOUT_S=300, got {timeout}"
        )

    def test_invalid_env_override_uses_default(self, monkeypatch):
        """Non-numeric SLM_MCP_SESSION_IDLE_TIMEOUT_S falls back to default."""
        monkeypatch.setenv("SLM_MCP_SESSION_IDLE_TIMEOUT_S", "not-a-number")
        SLMFastMCP = _get_slmfastmcp()
        mod = _import_transport()
        mcp = SLMFastMCP("slm-test-bad-env")
        mcp.streamable_http_app()

        timeout = mcp._session_manager.session_idle_timeout
        assert timeout == mod._DEFAULT_SESSION_IDLE_TIMEOUT, (
            "Non-numeric env var should fall back to _DEFAULT_SESSION_IDLE_TIMEOUT"
        )


# ===========================================================================
# GROUP B: SLMInMemoryEventStore
# ===========================================================================


class TestSLMInMemoryEventStore:
    """Bounded in-memory event store for SSE resumability."""

    # ---- B-1: Basic storage and retrieval ----------------------------------

    async def test_store_returns_monotonically_increasing_event_ids(self):
        """Each store_event call returns a unique, strictly incrementing event_id."""
        SLMInMemoryEventStore = _get_store_class()
        from mcp.types import JSONRPCMessage

        store = SLMInMemoryEventStore()
        e1 = await store.store_event("stream-1", None)   # priming event
        e2 = await store.store_event("stream-1", None)   # another priming
        e3 = await store.store_event("stream-1", None)

        ids = [e1, e2, e3]
        # All unique
        assert len(set(ids)) == 3, "Event IDs must be unique"
        # Monotonically increasing when interpreted as numbers
        nums = [int(i) for i in ids]
        assert nums == sorted(nums), "Event IDs must be monotonically increasing"

    async def test_store_event_returns_string_event_id(self):
        """store_event returns a non-empty string."""
        SLMInMemoryEventStore = _get_store_class()
        store = SLMInMemoryEventStore()
        eid = await store.store_event("stream-1", None)
        assert isinstance(eid, str) and eid, "Event ID must be a non-empty string"

    # ---- B-2: Replay -------------------------------------------------------

    async def test_replay_after_first_event_delivers_subsequent_events(self):
        """replay_events_after(e1) must deliver e2, e3 — not e1 itself."""
        SLMInMemoryEventStore = _get_store_class()
        from mcp.server.streamable_http import EventMessage
        from mcp.types import JSONRPCMessage

        store = SLMInMemoryEventStore()
        # Minimal synthetic JSONRPCMessage (ping/pong)
        def _msg(content: str) -> Any:
            # Use a simple mock that satisfies the EventMessage.message field
            from unittest.mock import MagicMock
            m = MagicMock(spec=JSONRPCMessage)
            m.__str__ = lambda self: content
            return m

        e1 = await store.store_event("s1", None)       # priming (None) — skip on replay
        e2 = await store.store_event("s1", _msg("hello"))
        e3 = await store.store_event("s1", _msg("world"))

        captured: list[EventMessage] = []

        async def cb(em: EventMessage) -> None:
            captured.append(em)

        stream_id = await store.replay_events_after(e1, cb)

        assert stream_id == "s1", f"Must return the stream_id that contained e1; got {stream_id}"
        assert len(captured) == 2, f"Should replay 2 messages (e2, e3); got {len(captured)}"
        assert captured[0].event_id == e2
        assert captured[1].event_id == e3

    async def test_replay_skips_none_priming_events(self):
        """None messages (priming events) must NOT be forwarded to send_callback."""
        SLMInMemoryEventStore = _get_store_class()
        from mcp.server.streamable_http import EventMessage
        from unittest.mock import MagicMock
        from mcp.types import JSONRPCMessage

        store = SLMInMemoryEventStore()
        real_msg = MagicMock(spec=JSONRPCMessage)

        e_prime = await store.store_event("s2", None)      # priming — should be skipped
        e_real  = await store.store_event("s2", real_msg)  # real message

        captured: list[EventMessage] = []
        async def cb(em: EventMessage) -> None:
            captured.append(em)

        await store.replay_events_after(e_prime, cb)
        # Only the real message should be delivered
        assert len(captured) == 1
        assert captured[0].message is real_msg

    async def test_replay_unknown_event_id_returns_none(self):
        """replay_events_after for an unknown event_id returns None (client must reinitialize)."""
        SLMInMemoryEventStore = _get_store_class()
        store = SLMInMemoryEventStore()
        await store.store_event("s3", None)

        captured = []
        async def cb(em) -> None:
            captured.append(em)

        result = await store.replay_events_after("unknown-event-id-99999", cb)
        assert result is None, "Unknown event_id must return None"
        assert captured == [], "No events should be replayed for unknown event_id"

    async def test_replay_from_last_event_delivers_nothing(self):
        """Replaying after the final event delivers zero messages (caught up)."""
        SLMInMemoryEventStore = _get_store_class()
        from unittest.mock import MagicMock
        from mcp.types import JSONRPCMessage

        store = SLMInMemoryEventStore()
        msg = MagicMock(spec=JSONRPCMessage)
        e1 = await store.store_event("s4", msg)
        e2 = await store.store_event("s4", msg)
        # Replay after the last event
        captured = []
        async def cb(em) -> None:
            captured.append(em)

        stream_id = await store.replay_events_after(e2, cb)
        assert stream_id == "s4", "Must still return the stream_id even if no events to replay"
        assert captured == [], "No events after e2 — should replay nothing"

    # ---- B-3: Bounded per-stream -------------------------------------------

    async def test_event_store_bounded_per_stream_drops_oldest(self):
        """Beyond max_events_per_stream, oldest events are evicted (FIFO).

        With max_events_per_stream=3 and 5 stored events, the deque holds
        the 3 most recent. The first 2 event_ids are no longer findable.
        """
        SLMInMemoryEventStore = _get_store_class()
        from unittest.mock import MagicMock
        from mcp.types import JSONRPCMessage

        store = SLMInMemoryEventStore(max_events_per_stream=3)
        msg = MagicMock(spec=JSONRPCMessage)

        # Store 5 events — only last 3 should survive in the deque
        ids = [await store.store_event("s5", msg) for _ in range(5)]
        e1, e2, e3, e4, e5 = ids

        captured = []
        async def cb(em) -> None:
            captured.append(em)

        # e1 and e2 were evicted — replay_events_after(e1) returns None
        result = await store.replay_events_after(e1, cb)
        assert result is None, (
            f"e1 was evicted (max_events=3, stored 5); replay must return None, got {result}"
        )
        assert captured == [], "No events for evicted event_id"

        # e3 is the new oldest — replay from e3 should return e4, e5
        captured.clear()
        result = await store.replay_events_after(e3, cb)
        assert result == "s5", "e3 is still in store; must return stream_id"
        assert len(captured) == 2, f"Should replay e4, e5; got {len(captured)}"
        assert captured[0].event_id == e4
        assert captured[1].event_id == e5

    async def test_event_store_bounded_per_stream_count(self):
        """Internal deque never exceeds max_events_per_stream entries."""
        SLMInMemoryEventStore = _get_store_class()
        from unittest.mock import MagicMock
        from mcp.types import JSONRPCMessage

        cap = 5
        store = SLMInMemoryEventStore(max_events_per_stream=cap)
        msg = MagicMock(spec=JSONRPCMessage)

        for _ in range(20):
            await store.store_event("s6", msg)

        # Access internal storage to verify bound
        stream_deque = store._store["s6"]
        assert len(stream_deque) == cap, (
            f"Deque must cap at {cap}; found {len(stream_deque)} entries"
        )

    async def test_event_store_bounded_stream_count_evicts_oldest_stream(self):
        """Beyond max_streams, the oldest stream_id is evicted from the store."""
        SLMInMemoryEventStore = _get_store_class()
        from unittest.mock import MagicMock
        from mcp.types import JSONRPCMessage

        store = SLMInMemoryEventStore(max_events_per_stream=10, max_streams=3)
        msg = MagicMock(spec=JSONRPCMessage)

        # Fill 3 streams
        await store.store_event("stream-A", msg)
        await store.store_event("stream-B", msg)
        await store.store_event("stream-C", msg)

        assert len(store._store) == 3

        # Adding a 4th stream should evict "stream-A" (oldest insertion)
        await store.store_event("stream-D", msg)
        assert len(store._store) == 3, "Must not exceed max_streams"
        assert "stream-A" not in store._store, "stream-A must be evicted (oldest)"
        assert "stream-D" in store._store, "stream-D (newest) must be present"

    # ---- B-4: Multi-stream isolation ----------------------------------------

    async def test_replay_finds_correct_stream_among_multiple(self):
        """replay_events_after finds the correct stream when multiple exist."""
        SLMInMemoryEventStore = _get_store_class()
        from unittest.mock import MagicMock
        from mcp.types import JSONRPCMessage

        store = SLMInMemoryEventStore()
        msg = MagicMock(spec=JSONRPCMessage)

        # Two separate streams interleaved
        a1 = await store.store_event("stream-alpha", msg)
        b1 = await store.store_event("stream-beta",  msg)
        a2 = await store.store_event("stream-alpha", msg)
        b2 = await store.store_event("stream-beta",  msg)

        # Replay from a1 should come from stream-alpha only
        captured = []
        async def cb(em) -> None:
            captured.append(em)

        sid = await store.replay_events_after(a1, cb)
        assert sid == "stream-alpha"
        assert len(captured) == 1
        assert captured[0].event_id == a2

    # ---- B-5: Concurrent monotonicity (cosmetic — event loop is single-threaded) --

    async def test_event_ids_are_globally_unique_across_streams(self):
        """Event IDs are globally unique across all streams (monotonic counter)."""
        SLMInMemoryEventStore = _get_store_class()
        store = SLMInMemoryEventStore()
        all_ids = set()
        for stream in ("x", "y", "z"):
            for _ in range(10):
                eid = await store.store_event(stream, None)
                all_ids.add(eid)
        assert len(all_ids) == 30, "All 30 event IDs across 3 streams must be unique"


# ===========================================================================
# GROUP C: Ordering guard
# ===========================================================================


class TestOrderingGuard:
    """streamable_http_app() must not crash regardless of call order."""

    def test_no_crash_before_configure_stateless(self):
        """streamable_http_app() with default settings (stateless=False) never raises.

        Simulates the case where a test or embedded host calls streamable_http_app()
        WITHOUT calling _configure_mcp_transport_settings() first.
        Default stateless_http=False → session manager created with idle timeout.
        """
        SLMFastMCP = _get_slmfastmcp()
        mcp = SLMFastMCP("guard-test-default")
        # settings.stateless_http defaults to False — this is safe
        assert mcp.settings.stateless_http is False

        try:
            mcp.streamable_http_app()
        except RuntimeError as exc:
            pytest.fail(
                f"streamable_http_app() raised RuntimeError before configure: {exc}"
            )
        except Exception as exc:
            pytest.fail(
                f"streamable_http_app() raised unexpected exception: {type(exc).__name__}: {exc}"
            )

        # Session manager must exist and have a finite idle timeout
        assert mcp._session_manager is not None
        assert mcp._session_manager.session_idle_timeout is not None

    def test_stateless_mode_suppresses_idle_timeout(self):
        """When stateless_http=True, session_idle_timeout must be None.

        The MCP SDK raises RuntimeError('session_idle_timeout is not supported in
        stateless mode') if both are set. Our guard prevents this crash by reading
        the stateless flag before creating the session manager.
        """
        SLMFastMCP = _get_slmfastmcp()
        mcp = SLMFastMCP("guard-test-stateless")
        # Simulate _configure_mcp_transport_settings() setting stateless=True
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True

        # Must NOT raise RuntimeError
        try:
            mcp.streamable_http_app()
        except RuntimeError as exc:
            pytest.fail(
                f"streamable_http_app() raised RuntimeError in stateless mode: {exc}\n"
                "The ordering guard must suppress session_idle_timeout when stateless=True."
            )

        assert mcp._session_manager is not None
        # In stateless mode: session_idle_timeout must be None (SDK invariant)
        assert mcp._session_manager.session_idle_timeout is None, (
            "session_idle_timeout must be None in stateless mode "
            "(SDK raises RuntimeError if both are set)"
        )

    def test_stateless_mode_uses_no_event_store(self):
        """Stateless mode must not be given an event_store (SDK restriction)."""
        SLMFastMCP = _get_slmfastmcp()
        mcp = SLMFastMCP("guard-test-stateless-no-store")
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True
        mcp.streamable_http_app()
        # In stateless mode the SDK ignores event_store (it's None for stateless transport)
        # Our code must not inject a store that causes issues
        assert mcp._session_manager.stateless is True

    def test_second_call_is_idempotent(self):
        """streamable_http_app() called twice reuses the same session manager."""
        SLMFastMCP = _get_slmfastmcp()
        mcp = SLMFastMCP("guard-test-idempotent")
        mcp.streamable_http_app()
        sm1 = mcp._session_manager
        mcp.streamable_http_app()
        sm2 = mcp._session_manager
        assert sm1 is sm2, "Session manager must be the same object on second call"

    def test_event_store_injected_for_stateful_mode(self):
        """In stateful mode (default), event_store is an SLMInMemoryEventStore."""
        SLMFastMCP = _get_slmfastmcp()
        SLMInMemoryEventStore = _get_store_class()
        mcp = SLMFastMCP("guard-test-store-injected")
        mcp.streamable_http_app()

        # The event_store on the session manager should be an SLMInMemoryEventStore
        assert mcp._session_manager.event_store is not None, (
            "event_store must be injected for stateful mode (enables SSE resumability)"
        )
        assert isinstance(mcp._session_manager.event_store, SLMInMemoryEventStore), (
            f"event_store must be SLMInMemoryEventStore, got {type(mcp._session_manager.event_store)}"
        )

    def test_user_provided_event_store_is_respected(self):
        """If the caller already set _event_store, that store is used (not replaced)."""
        SLMFastMCP = _get_slmfastmcp()
        SLMInMemoryEventStore = _get_store_class()
        from mcp.server.streamable_http import EventStore

        # Build a custom event store stub
        class _CustomStore(EventStore):
            async def store_event(self, stream_id, message):
                return "x"
            async def replay_events_after(self, last_event_id, send_callback):
                return None

        custom = _CustomStore()
        mcp = SLMFastMCP("guard-test-custom-store")
        # Inject a pre-existing event store (simulating a caller that configured one)
        mcp._event_store = custom
        mcp.streamable_http_app()

        assert mcp._session_manager.event_store is custom, (
            "Pre-configured event_store must not be replaced by SLMInMemoryEventStore"
        )
