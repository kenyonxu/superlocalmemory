"""Enabled proxy routes authenticate callers before any upstream request."""
# ruff: noqa: F811 -- imported pytest fixtures are requested as parameters

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@pytest.fixture
def proxy_app(engine_with_mock_deps, monkeypatch, request):
    from superlocalmemory.optimize.config import get_shared_store
    from superlocalmemory.optimize.config.schema import OptimizeConfig

    store = get_shared_store()
    monkeypatch.setattr(store, "get", lambda: OptimizeConfig.from_dict({
        "proxy_enabled": True, "cache_enabled": False, "compress_enabled": False,
    }))
    app = request.getfixturevalue("app_and_engine")
    assert app.state.optimize_proxy is not None
    sent = []

    async def upstream(req):
        sent.append(req)
        return httpx.Response(200, json={"choices": [], "ok": True})

    # Use the production pool, replacing only its wire transport.
    original_client = httpx.AsyncClient

    def offline_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(upstream)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", offline_client)
    proxy = app.state.optimize_proxy
    asyncio.run(proxy.startup())
    client = TestClient(app, base_url=f"http://127.0.0.1:{app.state.daemon_descriptor.port}",
                        client=("127.0.0.1", 51000))
    yield app, client, sent
    asyncio.run(proxy.shutdown())


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/chat/completions"), ("POST", "/v1/messages"),
    ("POST", "/v1/embeddings"), ("GET", "/v1/models"),
    ("POST", "/v1/messages/count_tokens"),
    ("POST", "/v1beta/models/gemini-test:generateContent"),
    ("POST", "/v1beta/openai/chat/completions"),
    ("GET", "/v1beta/openai/models"),
    ("POST", "/v1/projects/test/locations/us/publishers/google/models/test:generateContent"),
])
def test_proxy_requires_provider_credential(proxy_app, method, path):
    _, client, sent = proxy_app
    response = client.request(method, path, json={"model": "test", "messages": []})
    assert response.status_code == 401
    assert not sent


@pytest.mark.parametrize("origin", ["http://localhost:8765", "http://127.0.0.1:8767",
                                   "https://attacker.example"])
def test_proxy_refuses_foreign_browser_even_with_provider_key(proxy_app, origin):
    _, client, sent = proxy_app
    response = client.post("/v1/chat/completions", headers={
        "Origin": origin, "Authorization": "Bearer test-provider-credential",
    }, json={"model": "test", "messages": []})
    assert response.status_code == 403
    assert not sent


def test_proxy_own_origin_and_nonbrowser_provider_auth_work(proxy_app):
    app, client, sent = proxy_app
    for origin in (None, f"http://localhost:{app.state.daemon_descriptor.port}"):
        headers = {"Authorization": "Bearer test-provider-credential"}
        if origin:
            headers["Origin"] = origin
        assert client.post("/v1/chat/completions", headers=headers,
                           json={"model": "test", "messages": []}).status_code == 200
    assert len(sent) == 2
