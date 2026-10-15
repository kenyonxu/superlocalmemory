# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
"""SuperLocalMemory V3 - WebSocket Routes
 - AGPL-3.0-or-later

Routes: /ws/updates
"""
import logging
from datetime import datetime, timezone
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("superlocalmemory.routes.ws")
router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        self.active_connections -= disconnected


manager = ConnectionManager()


def _ws_origin_allowed(websocket: WebSocket) -> bool:
    """Return True when the WebSocket upgrade Origin is permitted.

    Non-browser clients (CLI, MCP tools) carry no Origin header and are
    still pass the network/account gates. Browser origins must belong to
    this listener, including explicitly configured remote dashboards.
    """
    origin = websocket.headers.get("origin", "")
    if not origin:
        return True  # non-browser caller (CLI/MCP) — no origin to validate

    try:
        from superlocalmemory.server.origin import (
            daemon_browser_origins,
            remote_browser_origin_allowed,
        )

        descriptor = getattr(websocket.app.state, "daemon_descriptor", None)
        port = getattr(descriptor, "port", None)
        if port is None:
            # The standalone dashboard uses the ASGI listener identity.
            port = (websocket.scope.get("server") or (None, None))[1]
        if port is not None and origin in daemon_browser_origins(int(port)):
            return True
        if port is not None and remote_browser_origin_allowed(
                origin, port=int(port), host_header=websocket.headers.get("host", "")):
            # The outer NonLocalAccessGate and the route's RBAC check still
            # decide caller authority; an allowed origin is never a credential.
            return True
    except Exception:
        return False

    return False


def _ws_rbac_allowed(websocket: WebSocket, app_state) -> bool:
    """Return True when the caller is authorised to open a WS connection.

    Mirrors _rbac_read_gate in unified_daemon.py: no-op in single-operator
    installations (zero RBAC users), requires a valid session in company /
    require_login mode.  Fails closed on RBAC errors.
    """
    rbac = getattr(app_state, "rbac", None)
    if rbac is None:
        return True  # single-operator mode — no auth layer active

    try:
        active = rbac.user_count() > 0
    except Exception:
        logger.warning("ws: RBAC state unavailable — rejecting handshake (fail closed)")
        return False  # fail closed

    if not active:
        return True  # no users registered — single-operator mode

    token = (
        websocket.headers.get("x-slm-user-session", "")
        or websocket.cookies.get("slm_session", "")
    )
    from superlocalmemory.server.rbac_enforce import presented_session_user

    try:
        user, refused = presented_session_user(rbac, token)
    except Exception:
        logger.warning("ws: session lookup failed — rejecting handshake (fail closed)")
        return False
    if refused:
        return False  # an ended session is not the owner (OPS-24)

    if user is None:
        if rbac.require_login():
            return False  # login required and no valid session
        return True  # personal / owner mode — no session needed

    try:
        from superlocalmemory.access.rbac import Permission
        from superlocalmemory.server.routes.helpers import get_active_profile
        return rbac.has_permission(user["user_id"], get_active_profile(), Permission.READ)
    except Exception:
        logger.warning("ws: permission check failed — rejecting handshake (fail closed)")
        return False


@router.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket):
    """WebSocket endpoint for real-time memory updates."""
    app_state = getattr(websocket, "app", None)
    app_state = getattr(app_state, "state", None)

    # --- Reject disallowed origins before accepting the handshake. ---
    if not _ws_origin_allowed(websocket):
        logger.info("ws: rejected handshake from disallowed origin %s",
                    websocket.headers.get("origin", ""))
        await websocket.close(code=1008)  # 1008 = Policy Violation
        return

    # --- Reject unauthenticated callers when login is required. ---
    if not _ws_rbac_allowed(websocket, app_state):
        logger.info("ws: rejected unauthenticated or unauthorised handshake")
        await websocket.close(code=4001)  # 4001 = custom: Unauthorized
        return

    await manager.connect(websocket)

    try:
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connection established",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        while True:
            try:
                data = await websocket.receive_json()

                if data.get('type') == 'ping':
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                elif data.get('type') == 'get_stats':
                    await websocket.send_json({
                        "type": "stats_update",
                        "message": "Use /api/stats endpoint for stats",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

            except WebSocketDisconnect:
                break
            except Exception:
                logger.exception("ws route error")
                await websocket.send_json({
                    "type": "error",
                    "message": "Internal server error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    finally:
        manager.disconnect(websocket)
