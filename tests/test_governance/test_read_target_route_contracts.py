# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""Read-target selection follows exact routes, aliases and included prefixes."""

import pytest
from fastapi import APIRouter, FastAPI, Query, Request, WebSocket

from superlocalmemory.server.read_targets import read_profiles


def _request(app, path, query):
    return Request({"type": "http", "app": app, "method": "GET", "path": path,
                    "query_string": query.encode(), "headers": []})


@pytest.mark.parametrize("path", ["/probe", "/probe/"])
def test_a_redirect_candidate_cannot_override_a_later_exact_route(monkeypatch, path):
    monkeypatch.setattr("superlocalmemory.server.routes.helpers.get_active_profile",
                        lambda: "default")
    app = FastAPI()
    alternate = path.rstrip("/") if path.endswith("/") else path + "/"

    @app.get(alternate)
    def named(profile: str = ""):
        return profile

    @app.get(path)
    def active():
        return "default"

    assert read_profiles(_request(app, path, "profile=sales")) == ("sales", "default")


def test_nested_included_router_preserves_the_query_alias():
    nested = APIRouter(prefix="/nested")

    @nested.get("/record/{record_id}")
    def named(record_id: str, workspace: str = Query("", alias="profile_id")):
        return workspace

    outer = APIRouter()
    outer.include_router(nested, prefix="/v3")
    app = FastAPI()
    app.include_router(outer, prefix="/api")
    request = _request(app, "/api/v3/nested/record/r1", "profile_id=sales")
    assert read_profiles(request) == ("sales",)


def test_a_real_slash_redirect_keeps_its_named_workspace():
    app = FastAPI()

    @app.get("/probe/")
    def named(profile: str = ""):
        return profile

    assert read_profiles(_request(app, "/probe", "profile=sales")) == ("sales",)


def test_websocket_routes_do_not_hide_later_http_selectors():
    app = FastAPI()

    @app.websocket("/socket")
    async def socket(websocket: WebSocket):
        pass

    @app.get("/named")
    def named(profile: str = ""):
        return profile

    assert read_profiles(_request(app, "/named", "profile=sales")) == ("sales",)


def test_a_final_empty_selector_reads_the_active_workspace(monkeypatch):
    monkeypatch.setattr("superlocalmemory.server.routes.helpers.get_active_profile",
                        lambda: "default")
    app = FastAPI()

    @app.get("/named")
    def named(profile: str = ""):
        return profile or "default"

    request = _request(app, "/named", "profile=sales&profile=")
    assert read_profiles(request) == ("sales", "default")
