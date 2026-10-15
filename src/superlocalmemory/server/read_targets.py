# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""Authorize the workspace the read handler actually selects (OPS-25).

A profile query only replaces the active workspace when the matched route
declares that selector. An unconsumed query is still checked, but cannot stand
in for the active workspace a handler reads. Resolve FastAPI's included routers
and parameter aliases directly so the policy follows the registered contract.
"""

from __future__ import annotations

from starlette.routing import compile_path


def _routes(routes, prefix=""):
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _routes(original.routes, prefix + (route.include_context.prefix or ""))
        elif getattr(route, "methods", None) and hasattr(route, "dependant"):
            yield route, prefix + route.path


def _profile_query_keys(request) -> set[str]:
    from superlocalmemory.server.read_gates import PROFILE_QUERY_KEYS

    try:
        router = request.app.router
        path = request.url.path
        paths = [path]
        if router.redirect_slashes and path != "/":
            paths.append(path.rstrip("/") if path.endswith("/") else path + "/")
        # The router searches every exact route before trying slash redirects.
        # An earlier alternate route must not lend its selectors to a later
        # exact handler that reads the active workspace.
        for candidate in paths:
            for route, route_path in _routes(router.routes):
                if request.method not in route.methods:
                    continue
                pattern, _, _ = compile_path(route_path)
                if pattern.fullmatch(candidate):
                    return {field.alias for field in route.dependant.query_params
                            if field.alias in PROFILE_QUERY_KEYS}
    except (AttributeError, TypeError):
        # No route contract is available: never authorize a different workspace
        # based only on an arbitrary query string.
        pass
    return set()


def read_profiles(request, *, explicit_profile: str | None = None) -> tuple[str, ...]:
    from superlocalmemory.server.read_gates import requested_profiles
    from superlocalmemory.server.routes.helpers import get_active_profile

    if explicit_profile:
        return (explicit_profile,)
    named = requested_profiles(request)
    if not named:
        return (get_active_profile(),)
    consumed = [request.query_params.get(key, "").strip() for key in _profile_query_keys(request)]
    actual = [value for value in consumed if value] or [get_active_profile()]
    return tuple(dict.fromkeys((*named, *actual)))
