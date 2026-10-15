"""Presence and tool-list updates owned by one initialized stdio transport."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid

from mcp_types import NotificationParams

from superlocalmemory import __version__
from superlocalmemory.infra import mcp_processes
from superlocalmemory.infra.data_root import canonical_data_root

logger = logging.getLogger(__name__)


async def run_stdio(server, run_transport) -> None:
    root, identifier = canonical_data_root(), uuid.uuid4().hex
    watcher = None

    async def initialized(ctx, params):
        nonlocal watcher
        if watcher is not None:
            return
        tools = [tool.name for tool in await server.list_tools()]
        host = mcp_processes.host_from_client(ctx.session.client_params)
        try:
            mcp_processes.register(
                root, identifier, pid=os.getpid(), host=host, version=__version__, tools=tools
            )
        except (OSError, RuntimeError):
            logger.exception("Could not record initialized local MCP connection")
            return
        watcher = asyncio.create_task(
            _watch(server, ctx.session, root, identifier, host, tools)
        )

    server._lowlevel_server.add_notification_handler(
        "notifications/initialized", NotificationParams, initialized
    )
    try:
        await run_transport()
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        mcp_processes.unregister(root, identifier)


async def _watch(server, session, root, identifier, host, tools) -> None:
    while True:
        await asyncio.sleep(2)
        if mcp_processes.reconnect_requested(root, identifier):
            # The SDK's stdin reader can remain blocked even after async
            # cancellation. Only this initialized stdio process exits; no PID
            # signals or machine-wide reaping can reach another installation.
            mcp_processes.unregister(root, identifier)
            os._exit(0)
        current = [tool.name for tool in await server.list_tools()]
        if current != tools:
            mcp_processes.register(
                root, identifier, pid=os.getpid(), host=host, version=__version__, tools=current
            )
            await session.send_tool_list_changed()
            tools = current
