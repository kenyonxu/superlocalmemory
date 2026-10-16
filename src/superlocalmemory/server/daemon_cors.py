"""CORS for the owned daemon origin; token bootstrap is same-origin only."""

from starlette.middleware.cors import CORSMiddleware
from starlette.types import Receive, Scope, Send


class DaemonCORSMiddleware(CORSMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Dashboard consumers use relative fetches. Token responses and their
        # preflights must never grant cross-origin browser access.
        if (scope.get("type") == "http"
                and scope.get("path", "").rstrip("/") == "/internal/token"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)
