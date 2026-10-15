"""Bounded, read-only HTTP MCP verification after owned-daemon discovery."""

from __future__ import annotations

import asyncio
from urllib.parse import urlencode


async def _initialize_and_list(url: str, timeout: float) -> list[str]:
    # Official SDK API opened 2026-10-11:
    # https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/src/mcp/client/streamable_http.py
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [tool.name for tool in tools.tools]


def verify_http_mcp(port: int, *, profile: str | None = None, timeout: float = 5) -> dict:
    from superlocalmemory.hooks.portable_kit import _check_daemon_health

    url = f"http://127.0.0.1:{port}/mcp/"
    if profile:
        url += "?" + urlencode({"profile": profile})
    receipt = {"verified": False, "url": url, "tools": [], "error": None}
    if not _check_daemon_health(port):
        return {**receipt, "error": "Owned daemon not reachable at /health"}
    try:
        tools = asyncio.run(asyncio.wait_for(_initialize_and_list(url, timeout), timeout))
        return {**receipt, "verified": True, "tools": tools}
    except Exception as exc:
        return {**receipt, "error": f"MCP initialize/list_tools failed ({type(exc).__name__})"}
