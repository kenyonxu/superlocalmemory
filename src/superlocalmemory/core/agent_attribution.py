# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Which agent saved a memory: the connection's name, or a name it merely claimed.

4.1.25 audit L-15 / G-M2. "Who saved what" was whatever the caller put in
``metadata.agent_id`` (or the ``agent_id`` argument of a tool call), so any
process could claim to be any agent, and the dashboard showed those claims next
to capability digests as if both were facts.

The connection is the better witness, when something vouches for it. The stdio
server's ``SLM_AGENT_ID`` is set by whoever configured the agent host. A name
in an HTTP MCP URL (``/mcp/<agent>``) is chosen by the caller on every request,
so it is only a claim unless that request also presents the daemon capability
(PR 248 review, finding 3: ``POST /mcp/claude-code/`` with no credential was
recorded as the "claude-code" connection). A vouched name rides on the daemon
request in :data:`CONNECTION_HEADER`, and the daemon believes it only from a
caller holding the daemon capability (a file only this computer's user can
read; the MCP server and the command line hold it, a web page does not). The
saved memory then records:

* ``agent_id``: the connection's name when there is one, else the claimed name;
* ``agent_id_source``: ``connection`` or ``claimed``;
* ``claimed_agent_id``: a different name the call asserted, kept, never trusted.
"""

from __future__ import annotations

from typing import Any

CONNECTION_HEADER = "X-SLM-Connection-Agent"
#: Not a connection: the stdio sentinel when no agent was configured.
UNNAMED = "mcp_client"


def connection_agent() -> str:
    """This process's (or this HTTP MCP request's) vouched connection name, or ``""``.

    An unvouched URL name is not returned here; tools still pass it on as the
    claimed ``agent_id``, so it is recorded as ``claimed``.
    """
    from superlocalmemory.mcp.agent_context import (
        current_agent_is_vouched,
        get_current_agent_id,
        sanitize_agent_id,
    )

    name = get_current_agent_id()
    if not name or name == UNNAMED or not current_agent_is_vouched():
        return ""
    return sanitize_agent_id(name)


def forwarded_headers(capability: str | None) -> dict[str, str]:
    """The connection header a daemon request carries (only with the capability)."""
    agent = connection_agent()
    return {CONNECTION_HEADER: agent} if agent and capability is not None else {}


def bind(metadata: dict[str, Any], connection: str) -> dict[str, Any]:
    """``metadata`` with the agent fields set from ``connection`` (may be ``""``)."""
    out = dict(metadata)
    # A caller cannot set these itself: they say how far the name is believed.
    out.pop("agent_id_source", None)
    out.pop("claimed_agent_id", None)
    claimed = str(out.get("agent_id") or "").strip()
    if connection:
        out["agent_id"] = connection
        out["agent_id_source"] = "connection"
        if claimed and claimed not in (connection, UNNAMED):
            out["claimed_agent_id"] = claimed
    elif claimed:
        out["agent_id_source"] = "claimed"
    return out


def audit_fields(metadata: dict[str, Any] | None) -> dict[str, str]:
    """The agent fields an audit entry records for a save with ``metadata``."""
    meta = metadata or {}
    agent = str(meta.get("agent_id") or "")
    if not agent:
        return {}
    if meta.get("agent_id_source") == "connection":
        fields = {"connection_agent": agent}
        if meta.get("claimed_agent_id"):
            fields["claimed_agent_id"] = str(meta["claimed_agent_id"])
        return fields
    return {"claimed_agent_id": agent}


__all__ = [
    "CONNECTION_HEADER", "UNNAMED", "audit_fields", "bind", "connection_agent",
    "forwarded_headers",
]
