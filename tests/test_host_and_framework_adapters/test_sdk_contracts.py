"""UX-39/40: run the SDKs' native response types without provider traffic."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from superlocalmemory.optimize.adapters import withSLM
from superlocalmemory.optimize.cache.manager import CacheManager
from superlocalmemory.optimize.config import _reset_config_store


@pytest.fixture(autouse=True)
def reset_sdk_state(tmp_path):
    _reset_config_store()
    CacheManager.reset_instance()
    from superlocalmemory.optimize.storage.db import CacheDB

    CacheManager.set_instance(CacheManager(db=CacheDB(tmp_path / "cache.db")))
    yield
    CacheManager.reset_instance()
    _reset_config_store()


def enable(tmp_path):
    (tmp_path / "optimize.json").write_text(json.dumps({"enabled": True, "cache_enabled": True}))


def sdk_client(provider, asynchronous, counts):
    def respond(request):
        counts.append(request)
        if provider == "openai":
            body = {
                "id": "chat-1",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        else:
            body = {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [{"type": "text", "text": "Hello"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        http = __import__("httpx2") if provider == "anthropic" else httpx
        return http.Response(200, json=body)

    module = pytest.importorskip(provider)
    name = ("Async" if asynchronous else "") + ("OpenAI" if provider == "openai" else "Anthropic")
    http = __import__("httpx2") if provider == "anthropic" else httpx
    http_client = (http.AsyncClient if asynchronous else http.Client)(
        transport=http.MockTransport(respond)
    )
    return getattr(module, name)(api_key="fixture", http_client=http_client)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.slow
def test_ux40_saved_config_and_typed_cache(tmp_path, provider, asynchronous):
    enable(tmp_path)
    counts = []
    original = sdk_client(provider, asynchronous, counts)
    wrapped = withSLM(original)
    assert wrapped is not original, "standalone factory must load the saved enabled config"
    create = wrapped.chat.completions.create if provider == "openai" else wrapped.messages.create
    kwargs = {
        "model": "gpt-4o" if provider == "openai" else "claude",
        "messages": [{"role": "user", "content": "Hi"}],
        "temperature": 0,
        "max_tokens": 10,
    }

    if provider == "anthropic":
        kwargs.pop("temperature")

    async def run():
        first = await create(**kwargs)
        second = await create(**kwargs)
        await original.close()
        return first, second

    if asynchronous:
        first, second = asyncio.run(run())
    else:
        first, second = create(**kwargs), create(**kwargs)
        original.close()
    assert len(counts) == 1
    assert type(first) is type(second)
    assert first.model_dump() == second.model_dump()
    assert first.id == second.id


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.slow
def test_ux40_disabled_is_passthrough(provider):
    original = sdk_client(provider, False, [])
    assert withSLM(original) is original
    original.close()


@pytest.mark.parametrize("document", ["docs/proxy-setup.md", "docs/optimize-cli.md"])
def test_ux39_documented_factory_imports(document):
    import_lines = [
        line
        for line in Path(document).read_text().splitlines()
        if line.startswith("from superlocalmemory.optimize.adapters")
    ]
    assert import_lines
    for line in import_lines:
        exec(line, {})


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.slow
def test_tools_bypass_cache(tmp_path, provider, asynchronous):
    enable(tmp_path)
    calls = []
    original = sdk_client(provider, asynchronous, calls)
    wrapped = withSLM(original)
    create = wrapped.chat.completions.create if provider == "openai" else wrapped.messages.create
    tool = (
        {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
        if provider == "openai"
        else {"name": "lookup", "input_schema": {"type": "object"}}
    )
    kwargs = {
        "model": "fixture-model",
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 10,
        "tools": [tool],
    }

    async def run():
        await create(**kwargs)
        await create(**kwargs)
        await original.close()

    if asynchronous:
        asyncio.run(run())
    else:
        create(**kwargs)
        create(**kwargs)
        original.close()
    assert len(calls) == 2
    assert CacheManager.get_instance().metrics.sets == 0


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.slow
def test_streaming_bypasses_cache(tmp_path, asynchronous, provider):
    from unittest.mock import AsyncMock, Mock

    enable(tmp_path)
    original = sdk_client(provider, asynchronous, [])
    native_create = (
        AsyncMock(return_value="stream") if asynchronous else Mock(return_value="stream")
    )
    resource = original.chat.completions if provider == "openai" else original.messages
    resource.create = native_create
    wrapped = withSLM(original)
    create = wrapped.chat.completions.create if provider == "openai" else wrapped.messages.create
    kwargs = {"model": "fixture", "messages": [], "stream": True}

    async def run():
        assert await create(**kwargs) == "stream"
        assert await create(**kwargs) == "stream"
        await original.close()

    if asynchronous:
        asyncio.run(run())
    else:
        assert create(**kwargs) == "stream"
        assert create(**kwargs) == "stream"
        original.close()
    assert native_create.call_count == 2
    assert CacheManager.get_instance().metrics.sets == 0


@pytest.mark.slow
def test_cache_does_not_cross_provider_endpoints(tmp_path):
    enable(tmp_path)
    first_calls, second_calls = [], []
    first = sdk_client("openai", False, first_calls)
    second = sdk_client("openai", False, second_calls)
    second.base_url = "http://second-provider.invalid/v1/"
    kwargs = {
        "model": "same-model",
        "messages": [{"role": "user", "content": "Hi"}],
        "temperature": 0,
    }
    withSLM(first).chat.completions.create(**kwargs)
    withSLM(second).chat.completions.create(**kwargs)
    first.close()
    second.close()
    assert len(first_calls) == len(second_calls) == 1


@pytest.mark.parametrize("document", ["docs/proxy-setup.md", "docs/optimize-cli.md"])
@pytest.mark.slow
def test_literal_sdk_wrapper_quickstart(tmp_path, monkeypatch, document):
    import re

    import anthropic
    import openai

    enable(tmp_path)
    first = sdk_client("openai", False, [])
    second = sdk_client("anthropic", False, [])
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: first)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: second)
    snippets = re.findall(r"```python\n(.*?)```", Path(document).read_text(), re.S)
    snippet = next(s for s in snippets if "withSLM" in s)
    try:
        exec(snippet, {})
    finally:
        first.close()
        second.close()
