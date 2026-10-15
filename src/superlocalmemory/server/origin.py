"""Exact browser-origin validation for the local dashboard."""

from __future__ import annotations

from urllib.parse import urlsplit

from superlocalmemory.server.loopback import is_loopback as _is_loopback_host


def daemon_browser_origins(port: int) -> tuple[str, ...]:
    """The HTTP browser origins owned by this listener (never the redirect)."""
    if not 1 <= port <= 65535 or port == 8767:
        return ()
    return (f"http://localhost:{port}", f"http://127.0.0.1:{port}",
            f"http://[::1]:{port}")


def _origin_matches_request_host(host: str, host_header: str, port: int) -> bool:
    """A Host authority naming the same browser host and owned port."""
    if not host_header or any(char in host_header for char in "/?#"):
        return False
    try:
        requested = urlsplit(f"http://{host_header}")
        return (
            requested.hostname == host and requested.port == port
            and requested.username is None and requested.password is None
            and not requested.path and not requested.query and not requested.fragment
        )
    except (TypeError, ValueError):
        return False


def remote_browser_origin_allowed(origin: str, *, port: int, host_header: str = "") -> bool:
    """A configured LAN dashboard at this listener's owned port.

    Honor HTTP's exact names and bounded IP networks/prefixes. The documented
    global wildcard permits only a same-Host browser, not a foreign Origin.
    Origin admission grants neither caller authentication nor HTTP CORS.
    """
    from ipaddress import ip_address

    from superlocalmemory.core.remote_mode import (
        _allowlist_entries,
        _host_matches,
        _strip_port,
        is_remote_origin_allowed,
    )

    if not 1 <= port <= 65535 or port == 8767:
        return False
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.port != port
            or not parsed.hostname
            or "*" in parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
        ):
            return False
        if not is_remote_origin_allowed(origin):
            return False
        host = parsed.hostname.lower()
        entries = {_strip_port(entry).lower() for entry in _allowlist_entries()}
        if host in {entry for entry in entries if "*" not in entry and "/" not in entry}:
            return True
        if "*" in entries and _origin_matches_request_host(host, host_header, port):
            return True
        # Prefixes describe IP addresses, never domains that resemble an IP.
        client_ip = ip_address(host)
        return any(
            _host_matches(entry, host, client_ip)
            for entry in entries
            if entry != "*" and ("/" in entry or entry.endswith("*"))
        )
    except (TypeError, ValueError):
        return False


def origin_is_loopback(origin: str) -> bool:
    """Return whether an Origin is absent or an exact HTTP(S) loopback URL.

    Uses the centralized is_loopback helper so that IPv4-mapped loopback
    addresses (::ffff:127.x.x.x) are accepted correctly (issue #90).
    Note: browsers normalize Origin hostnames; the ::ffff: form would only
    appear in synthetic requests. The helper is used for defense-in-depth.
    """
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        # Accessing port validates malformed/non-numeric port values.
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and _is_loopback_host(parsed.hostname.lower())
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def origin_is_daemon(origin: str, *, port: int) -> bool:
    """Return whether ``origin`` is one of this daemon's loopback aliases.

    A loopback host alone is not a browser trust boundary: another local web
    server can run on a different port.  Credentialless dashboard writes are
    therefore limited to the port owned by this daemon.  Authenticated local
    integrations are handled separately by the write-identity boundary.
    """
    if not origin_is_loopback(origin):
        return False
    try:
        parsed = urlsplit(origin)
        return parsed.port == port
    except (TypeError, ValueError):
        return False


__all__ = ("origin_is_daemon", "origin_is_loopback")
