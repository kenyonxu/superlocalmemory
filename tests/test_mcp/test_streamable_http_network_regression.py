"""Network regression for Streamable-HTTP tool-result completion.

This intentionally uses the official MCP Streamable HTTP client over a real
Uvicorn socket.  Starlette's in-process TestClient does not exercise the
response lifecycle that previously left stateful SSE tool responses open on
Python 3.14.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
from collections.abc import AsyncIterator
from typing import Callable

import pytest


@contextlib.asynccontextmanager
async def _running_mcp_server(
    register_tools: Callable[[object], None] | None = None,
) -> AsyncIterator[str]:
    """Serve a stateful SLM MCP app on a loopback socket for one test."""
    import uvicorn
    from fastapi import FastAPI

    from superlocalmemory.mcp.http_transport import SLMFastMCP

    mcp = SLMFastMCP("streamable-http-regression")
    # This is the production default: stateful MCP over Streamable HTTP.
    mcp.settings.streamable_http_path = "/"
    mcp.settings.stateless_http = False
    mcp.settings.json_response = False

    if register_tools is None:
        @mcp.tool()
        async def recall(query: str, limit: int = 2) -> dict[str, object]:
            """Return a deliberately non-trivial response without a live database."""
            return {
                "query": query,
                "limit": limit,
                "results": [{"content": "x" * 65_536, "score": 0.99}],
            }
    else:
        register_tools(mcp)

    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", mcp_app)

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    _, port = probe.getsockname()
    probe.close()

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started, "Uvicorn did not start the MCP regression server"
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_stateful_streamable_http_tool_result_completes_over_network() -> None:
    """A large tools/call result must complete through the official client."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with _running_mcp_server() as endpoint:
        started = asyncio.get_running_loop().time()
        async with streamable_http_client(endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=5)
                tools = await asyncio.wait_for(session.list_tools(), timeout=5)
                assert any(tool.name == "recall" for tool in tools.tools)

                result = await asyncio.wait_for(
                    session.call_tool(
                        "recall", {"query": "transport regression", "limit": 2}
                    ),
                    timeout=5,
                )

        elapsed = asyncio.get_running_loop().time() - started
        assert not result.isError
        assert result.content
        assert elapsed < 5, f"Streamable HTTP tools/call took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_core_recall_resolves_daemon_proxy_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mounted HTTP recall must not synchronously probe the daemon on its own loop."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from superlocalmemory.mcp import _daemon_proxy
    from superlocalmemory.mcp.tools_core import register_core_tools

    event_loop_thread = threading.get_ident()
    factory_threads: list[int] = []

    class _Pool:
        def recall(self, **kwargs: object) -> dict[str, object]:
            return {
                "ok": True,
                "results": [{"content": "x" * 65_536, "score": 0.99}],
                "result_count": 1,
                "query_type": "semantic",
                "channel_weights": {"semantic": 1.0},
            }

    def _choose_pool() -> _Pool:
        factory_threads.append(threading.get_ident())
        return _Pool()

    monkeypatch.setattr(_daemon_proxy, "choose_pool", _choose_pool)

    async with _running_mcp_server(
        lambda server: register_core_tools(server, lambda: None)
    ) as endpoint:
        async with streamable_http_client(endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=5)
                result = await asyncio.wait_for(
                    session.call_tool("recall", {"query": "transport regression"}),
                    timeout=5,
                )

    assert not result.isError
    assert result.content
    assert factory_threads
    assert all(thread_id != event_loop_thread for thread_id in factory_threads)
