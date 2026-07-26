# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Resource-safe FastMCP Streamable-HTTP integration — Workstream E additions.

MCP SDK 1.27.1 passes an AnyIO ``MemoryObjectReceiveStream`` directly to
``EventSourceResponse`` for every JSON-RPC POST.  The response consumes that
stream but does not close it after normal iteration, leaving one receive
endpoint per request for the garbage collector.  The response is the owner of
that per-request iterator, so SLM closes it at the response boundary.

Workstream E (3.8.4): MCP Connection Resilience
------------------------------------------------
Two root causes of frequent MCP disconnects are addressed here:

RC-2: No session idle timeout — zombie sessions accumulate forever.
    Fix: ``SLMFastMCP.streamable_http_app()`` pre-creates the
    ``StreamableHTTPSessionManager`` with a finite ``session_idle_timeout``
    (default 600 s, overridable via ``SLM_MCP_SESSION_IDLE_TIMEOUT_S``).

RC-3: No EventStore — every SSE drop requires a full re-initialize.
    Fix: ``SLMInMemoryEventStore`` (bounded, in-memory, async-safe) is
    injected as the session manager's event store.  A dropped SSE stream can
    resume via ``Last-Event-ID`` instead of forcing a new ``initialize``
    handshake.

KNOWN LIMITATIONS (not fixed here, documented for future work):
    * RC-1 (mcp-remote orphan test-sessions): The mcp-remote v0.1.38 bug
      creates a ``testTransport``/``testClient`` that is never closed.  This
      leaks one zombie session per mcp-remote startup.  Tracked upstream.
      Session idle-timeout mitigates the accumulation.
    * Client reconnect after daemon restart: ``SLMInMemoryEventStore`` is
      in-memory only and does not survive daemon process restart.  After a
      restart, stale ``Last-Event-ID`` values are unknown to the new store;
      ``replay_events_after`` returns ``None`` and the client must
      re-initialize.  A SQLite-backed EventStore is planned for v3.9.
    * Claude Code client bug Anthropic #48557: Claude Code may send a stale
      MCP session ID after server restart.  This is a client-side defect;
      SLM cannot fix it from the server.
    * Event-loop stall during background maintenance (RC-6): The pruner-lock
      stall fix lives in Workstream A+F (already merged into this branch).
      This module depends on that fix; see fix/3.8.4 merge commit.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict, deque
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import (
    EventCallback,
    EventId,
    EventMessage,
    EventStore,
    StreamId,
)
from sse_starlette.sse import EventSourceResponse
from starlette.types import Receive, Scope, Send

from superlocalmemory import __version__

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session idle timeout — configurable default
# ---------------------------------------------------------------------------

#: Default session idle timeout in seconds.
#:
#: Rationale for 600 s (10 minutes), not the SDK's suggested 1800 s:
#:   * At 1800 s, with mcp-remote leaking 3 zombie sessions per conversation
#:     start and a typical conversation cadence of one every 15 min,
#:     up to (1800/15) × 3 = 360 zombie sessions can accumulate in the worst
#:     case before the first one expires.  600 s bounds this to ~6.
#:   * 600 s is well above the 15 s SSE keepalive interval, the typical
#:     in-conversation idle gap (<5 min), and the mcp-remote reconnect window.
#:   * Active sessions push their idle deadline forward on every tool call,
#:     so a user making any request within 10 min never loses their session.
#:   * A session idle for exactly 10 min (user stepped away) is reaped and
#:     recreated transparently on next tool call via mcp-remote reconnect.
#:
#: CRIT — anti-patterns to avoid:
#:   Too aggressive (<60 s): frequent idle-deadline checks waste CPU in
#:     AnyIO's CancelScope machinery; legitimate quiet users are interrupted.
#:   Too lenient (>7200 s = 2 h): zombie sessions accumulate at rates that
#:     can reach 100+ entries, degrading AnyIO task-group scheduling.
_DEFAULT_SESSION_IDLE_TIMEOUT: float = 600.0


def _slm_session_idle_timeout() -> float:
    """Return the configured MCP session idle timeout in seconds.

    Reads ``SLM_MCP_SESSION_IDLE_TIMEOUT_S`` from the environment.
    Falls back to :data:`_DEFAULT_SESSION_IDLE_TIMEOUT` on missing or
    non-numeric values.  A value ≤ 0 is also treated as the default.
    """
    raw = os.environ.get("SLM_MCP_SESSION_IDLE_TIMEOUT_S", "").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            logger.warning(
                "SLM_MCP_SESSION_IDLE_TIMEOUT_S=%r is not a valid number; "
                "using default %s s",
                raw,
                _DEFAULT_SESSION_IDLE_TIMEOUT,
            )
    return _DEFAULT_SESSION_IDLE_TIMEOUT


# ---------------------------------------------------------------------------
# SLMInMemoryEventStore — bounded, in-memory, async-safe
# ---------------------------------------------------------------------------


class SLMInMemoryEventStore(EventStore):
    """Bounded in-memory event store for MCP SSE resumability.

    Enables clients to reconnect to the GET /mcp SSE endpoint and replay
    missed events via the ``Last-Event-ID`` header instead of performing a
    full ``initialize`` handshake (which creates a new session and adds to
    the zombie-session count).

    Design choices:
    ---------------
    * **Per-stream bounded deque** — each stream_id maps to a
      ``deque(maxlen=max_events_per_stream)``.  Oldest events are evicted
      automatically (FIFO) when the cap is hit.
    * **Global stream count cap** — the store tracks at most
      ``max_streams`` distinct stream_ids.  When exceeded, the least-recently
      used stream is evicted from the dict.  This bounds total memory usage.
    * **Async-safe, lock-free** — all access happens on a single asyncio event
      loop; no ``asyncio.Lock`` is needed.
    * **Priming events (message=None) are stored but skipped during replay** —
      the SDK mints a fresh priming event in the replay path (see
      ``StreamableHTTPServerTransport._replay_events``).

    Memory bound:
    -------------
    ``max_events_per_stream`` × ~2 KB (avg JSONRPCMessage) × ``max_streams``
    = 200 × 2 KB × 100 = ~40 MB in the absolute worst case.
    At steady state with idle-timeout reaping sessions after 10 min, the
    typical load is 3–6 active streams × 200 events × 2 KB ≈ 1.2–2.4 MB.

    Known limitation:
    -----------------
    This store is in-memory only.  It does not survive a daemon process
    restart.  After a restart, any ``Last-Event-ID`` from a previous process
    is unknown; ``replay_events_after`` returns ``None`` and the client must
    re-initialize.  A SQLite-backed EventStore is planned for v3.9.
    """

    def __init__(
        self,
        max_events_per_stream: int = 200,
        max_streams: int = 100,
    ) -> None:
        """Initialise the bounded event store.

        Args:
            max_events_per_stream: Maximum number of events stored per
                stream before oldest events are dropped.  Default 200
                (~400 KB per stream at 2 KB/event).
            max_streams: Maximum number of distinct stream_ids tracked.
                When exceeded, the least-recently used stream is evicted
                (all its events are lost).  Default 100.
        """
        self._max_events = max_events_per_stream
        self._max_streams = max_streams
        # OrderedDict maintains insertion/access order for LRU eviction.
        # stream_id → deque[(event_id, message)]
        self._store: OrderedDict[str, deque] = OrderedDict()
        # Monotonically incrementing counter; single event loop → no lock needed.
        self._counter: int = 0

    # ------------------------------------------------------------------
    # EventStore ABC implementation
    # ------------------------------------------------------------------

    async def store_event(
        self,
        stream_id: StreamId,
        message: Any,  # JSONRPCMessage | None
    ) -> EventId:
        """Store an event for the given stream and return its unique event_id.

        Args:
            stream_id: The stream (GET /mcp SSE connection) this event belongs to.
            message: The JSON-RPC message, or ``None`` for priming events.

        Returns:
            A monotonically incrementing string event_id.
        """
        self._counter += 1
        event_id: EventId = str(self._counter)

        if stream_id not in self._store:
            # Evict oldest stream if at capacity
            if len(self._store) >= self._max_streams:
                oldest_stream, _ = self._store.popitem(last=False)
                logger.debug(
                    "SLMInMemoryEventStore: evicted oldest stream %r "
                    "(max_streams=%d reached)",
                    oldest_stream,
                    self._max_streams,
                )
            self._store[stream_id] = deque(maxlen=self._max_events)
        else:
            # Move to "most recently used" end so LRU eviction works correctly
            self._store.move_to_end(stream_id)

        self._store[stream_id].append((event_id, message))
        return event_id

    async def replay_events_after(
        self,
        last_event_id: EventId,
        send_callback: EventCallback,
    ) -> StreamId | None:
        """Replay events that occurred after ``last_event_id``.

        Searches all tracked streams for ``last_event_id`` and forwards every
        subsequent non-None event to ``send_callback``.  Priming events
        (``message=None``) are skipped; the SDK mints a fresh priming event
        in the replay path.

        Args:
            last_event_id: The ID of the last event the client received.
            send_callback: Async callback that receives each missed
                ``EventMessage`` in order.

        Returns:
            The stream_id that contained ``last_event_id``, or ``None`` if
            the event was not found (client must re-initialize).
        """
        for stream_id, events in self._store.items():
            found = False
            for event_id, message in events:
                if found:
                    if message is not None:
                        await send_callback(EventMessage(message=message, event_id=event_id))
                elif event_id == last_event_id:
                    found = True
            if found:
                return stream_id
        # last_event_id not in any stream (evicted or never seen)
        return None


# ---------------------------------------------------------------------------
# SSE resource guard (unchanged from pre-E)
# ---------------------------------------------------------------------------


class ClosingEventSourceResponse(EventSourceResponse):
    """EventSourceResponse that closes the async iterator it consumes."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                await close()


def install_streamable_http_resource_guard() -> None:
    """Install the response owner used by MCP's Streamable-HTTP transport."""
    from mcp.server import streamable_http

    streamable_http.EventSourceResponse = ClosingEventSourceResponse


# ---------------------------------------------------------------------------
# SLMFastMCP — session lifecycle + SSE cleanup
# ---------------------------------------------------------------------------


class SLMFastMCP(FastMCP):
    """FastMCP with SLM release identity, deterministic SSE cleanup, and
    session lifecycle management (Workstream E).

    Additions over the base FastMCP:

    1. **Session idle timeout**: the ``StreamableHTTPSessionManager`` is
       pre-created with a finite ``session_idle_timeout`` so zombie sessions
       (from mcp-remote orphan test-connects, or abandoned conversations) are
       automatically reaped instead of accumulating forever.

    2. **Bounded event store**: a ``SLMInMemoryEventStore`` is injected so
       that a dropped SSE stream can resume via ``Last-Event-ID`` without a
       full ``initialize`` round-trip.

    3. **Ordering guard**: ``streamable_http_app()`` reads the ``stateless_http``
       flag from ``self.settings`` at call time.  If the flag is ``True``
       (set by ``_configure_mcp_transport_settings()`` in unified_daemon.py),
       ``session_idle_timeout`` is suppressed — the MCP SDK raises
       ``RuntimeError`` if both are set simultaneously.  If
       ``streamable_http_app()`` is called before settings are configured
       (e.g. in unit tests), the default ``stateless_http=False`` applies
       safely.
    """

    def __init__(self, *args, product_version: str = __version__, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # FastMCP delegates the initialize response to the low-level MCP
        # server. Without an explicit value it reports the installed ``mcp``
        # library version, which makes an SLM 3.7 server identify itself as
        # (for example) 1.27.1 to every IDE client.
        self._mcp_server.version = product_version

    def streamable_http_app(self):  # type: ignore[override]
        """Return the Streamable-HTTP Starlette app.

        Pre-creates the ``StreamableHTTPSessionManager`` with SLM-specific
        parameters before delegating to ``super()`` (which skips re-creation
        because the manager is already set).

        Ordering guarantee
        ------------------
        In production ``unified_daemon.py`` always calls
        ``_configure_mcp_transport_settings(fastmcp)`` *before* this method,
        so ``self.settings.stateless_http`` reflects the correct runtime value.
        If this method is called first (unit tests, embedded hosts), the
        default ``stateless_http=False`` is used — safe because the stateless
        guard only matters when ``stateless_http=True`` (avoid SDK RuntimeError).

        Dependency note (A+F)
        ---------------------
        This method assumes that ``fix/3.8.4-A+F`` (already merged into this
        branch) has eliminated the pruner-lock stall that caused event-loop
        starvation during background maintenance.  Session idle-timeout and
        the event store improve resilience to transient drops, but they cannot
        compensate for a fully stalled event loop.
        """
        install_streamable_http_resource_guard()

        if self._session_manager is None:
            from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

            # Ordering guard: read stateless_http defensively.
            # ``_configure_mcp_transport_settings()`` may not have been called
            # yet; ``getattr`` with a False default ensures we never pass
            # session_idle_timeout=<value> into a stateless manager (SDK raises
            # RuntimeError if both are set simultaneously).
            is_stateless: bool = getattr(self.settings, "stateless_http", False)

            # Idle timeout: finite for stateful sessions; None for stateless
            # (stateless sessions have no identity — there is nothing to reap).
            idle_timeout: float | None = (
                None if is_stateless else _slm_session_idle_timeout()
            )

            # Event store: inject SLMInMemoryEventStore for stateful mode.
            # Stateless mode must not receive an event store (SDK limitation).
            # If the caller already configured an event store (via FastMCP
            # constructor argument), respect it — do not replace with ours.
            if is_stateless:
                event_store: EventStore | None = None
            else:
                event_store = self._event_store or SLMInMemoryEventStore()

            self._session_manager = StreamableHTTPSessionManager(
                app=self._mcp_server,
                event_store=event_store,
                retry_interval=self._retry_interval,
                json_response=self.settings.json_response,
                stateless=is_stateless,
                security_settings=self.settings.transport_security,
                session_idle_timeout=idle_timeout,
            )
            logger.info(
                "SLM MCP session manager created: stateless=%s, "
                "idle_timeout=%ss, event_store=%s",
                is_stateless,
                idle_timeout,
                type(event_store).__name__ if event_store else "None",
            )

        return super().streamable_http_app()
