"""Proxy memory bodies use the screened outbound door on every forwarding path."""
# ruff: noqa: F811 -- imported pytest fixtures are requested as parameters

import json
from types import SimpleNamespace

import httpx
import pytest

from tests.test_security.test_proxy_boundary_integration import proxy_app  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@pytest.mark.parametrize("path,stream", [
    ("/v1/chat/completions", False), ("/v1/chat/completions", True),
    ("/v1/messages", False), ("/v1/messages", True),
    ("/v1/embeddings", False), ("/v1/messages/count_tokens", False),
    ("/v1beta/models/gemini-test:generateContent", False),
    ("/v1beta/models/gemini-test:streamGenerateContent", True),
    ("/v1beta/openai/chat/completions", False),
    ("/v1/projects/test/locations/us/publishers/google/models/test:generateContent", False),
])
def test_all_proxy_forwarding_screens_memory_text(proxy_app, path, stream):
    _, client, sent = proxy_app
    synthetic = "ghp_" + "A" * 36
    body = {"model": "test", "stream": stream,
            "messages": [{"role": "user", "content": "memory marker " + synthetic}],
            "contents": [{"parts": [{"text": synthetic}]}], "input": synthetic}
    response = client.post(path, headers={
        "Authorization": "Bearer test-provider-credential",
        "x-api-key": "test-provider-credential", "x-goog-api-key": "test-provider-credential",
    }, json=body)
    assert response.status_code == 200
    assert sent
    for outbound in sent:
        leaked = synthetic.encode() in outbound.content
        assert leaked is False
        assert b"[redacted]" in outbound.content
        assert int(outbound.headers["content-length"]) == len(outbound.content)


def test_proxy_fail_open_still_screens_and_drops_local_credentials(proxy_app, monkeypatch):
    from superlocalmemory.optimize.proxy import openai_surface

    def broken_hook(*args, **kwargs):
        raise RuntimeError("forced pipeline failure")

    monkeypatch.setattr(openai_surface, "_body_has_tools", broken_hook)
    _, client, sent = proxy_app
    synthetic = "ghp_" + "B" * 36
    response = client.post("/v1/chat/completions", headers={
        "Authorization": "Bearer test-provider-credential",
        "X-Install-Token": "test-local-only", "X-SLM-User-Session": "test-local-session",
        "Cookie": "slm_session=test-local-session",
    }, content=json.dumps({"model": "test", "messages": [{"content": synthetic}]}))
    assert response.status_code == 200
    assert len(sent) == 1
    leaked = synthetic.encode() in sent[0].content
    assert leaked is False
    for name in ("x-install-token", "x-slm-user-session", "cookie"):
        assert name not in sent[0].headers


def test_proxy_cannot_fail_open_an_unscreenable_body(proxy_app):
    _, client, sent = proxy_app
    response = client.post("/v1/chat/completions", headers={
        "Authorization": "Bearer test-provider-credential",
    }, content=b"plain memory body that is not JSON")
    assert response.status_code == 502
    assert not sent


@pytest.mark.asyncio
async def test_gated_pool_closes_both_clients_when_one_close_fails():
    from superlocalmemory.core.outbound_http import GatedAsyncClient

    closed = []

    async def broken():
        closed.append("first")
        raise RuntimeError("mock close failed")

    async def working():
        closed.append("second")

    pool = GatedAsyncClient()
    pool._clients = {True: SimpleNamespace(aclose=broken), False: SimpleNamespace(aclose=working)}
    with pytest.raises(RuntimeError, match="mock close failed"):
        await pool.aclose()
    assert closed == ["first", "second"]


@pytest.mark.asyncio
async def test_gated_pool_never_redirects_or_proxies_loopback(monkeypatch, free_port):
    from superlocalmemory.core.outbound_http import GatedAsyncClient

    options = []
    sent = []
    original = httpx.AsyncClient

    def build(**kwargs):
        options.append(kwargs)
        return original(**kwargs)

    async def redirect(request):
        sent.append(request)
        return httpx.Response(302, headers={"Location": "https://unreviewed.example"})

    monkeypatch.setattr(httpx, "AsyncClient", build)
    pool = GatedAsyncClient(transport=httpx.MockTransport(redirect))
    try:
        # Transport is mocked; this URL cannot connect to a live service.
        response = await pool.post(f"http://127.0.0.1:{free_port}/fixture",
                                   content=b"local unscreened bytes", follow_redirects=True)
        assert response.status_code == 302
        assert len(sent) == 1
        assert options[0]["trust_env"] is False
        assert options[0]["follow_redirects"] is False
    finally:
        await pool.aclose()
