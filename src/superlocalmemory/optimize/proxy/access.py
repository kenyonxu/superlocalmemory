"""Provider authentication and browser-origin checks for every proxy surface."""

from fastapi import HTTPException, Request


async def require_proxy_access(request: Request) -> None:
    from superlocalmemory.server.origin import daemon_browser_origins

    origin = request.headers.get("origin")
    descriptor = getattr(request.app.state, "daemon_descriptor", None)
    port = getattr(descriptor, "port", None) or request.url.port
    if origin and (port is None or origin not in daemon_browser_origins(int(port))):
        raise HTTPException(403, detail="cross-origin request rejected")

    path = request.url.path
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    bearer = scheme.lower() == "bearer" and bool(credential.strip())
    if path.startswith("/v1/messages") or path == "/v1/models":
        authenticated = bool(request.headers.get("x-api-key", "").strip()) or bearer
    elif path.startswith("/v1beta/models/"):
        authenticated = bool(request.headers.get("x-goog-api-key", "").strip()) or bearer
    else:
        authenticated = bearer
    if not authenticated:
        raise HTTPException(401, detail="provider credential required")
