"""SLM OpenAI SDK adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from superlocalmemory.optimize.adapters.request_identity import cache_request
from superlocalmemory.optimize.adapters.response_codec import decode_response, encode_response

if TYPE_CHECKING:
    from superlocalmemory.optimize.cache.manager import CacheManager
    from superlocalmemory.optimize.config.schema import OptimizeConfig

logger = logging.getLogger(__name__)


def _detect_async_openai(client: Any) -> bool:
    try:
        from openai import AsyncOpenAI
        return isinstance(client, AsyncOpenAI)
    except ImportError:
        return False


class SLMOpenAIAdapter:
    """Wraps openai.OpenAI (or AsyncOpenAI) with SLM cache hooks."""

    def __init__(
        self,
        client: Any,
        cache_manager: "CacheManager",
        config: "OptimizeConfig",
        tenant_id: str = "default",
    ) -> None:
        self._original = client
        self._cache_manager = cache_manager
        self._cache_view = cache_manager.for_tenant(tenant_id)
        self._config = config
        self._tenant_id = tenant_id
        self._is_async: bool = _detect_async_openai(client)
        self.chat = _ChatCompletionsProxy(self)

    def _cache_request(self, kwargs: dict) -> dict:
        return cache_request(self._original, "openai", kwargs)

    def _build_cache_key(self, kwargs: dict) -> str | None:
        return self._cache_manager.build_key(self._cache_request(kwargs), self._tenant_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    def _should_cache(self, kwargs: dict) -> bool:
        if kwargs.get("stream", False):
            return False
        if kwargs.get("tools") or kwargs.get("functions"):
            return False
        return True

    def _chat_create_sync(self, **kwargs: Any) -> Any:
        if not self._config.cache_enabled or not self._should_cache(kwargs):
            return self._original.chat.completions.create(**kwargs)
        key = self._build_cache_key(kwargs)
        if key is None:
            return self._original.chat.completions.create(**kwargs)
        try:
            cached = self._cache_manager.get(self._cache_request(kwargs), self._tenant_id)
            hit = cached.data if cached is not None and cached.hit else None
        except Exception as exc:
            logger.warning("SLM cache read failed: %s", exc)
            hit = None
        if hit is not None:
            try:
                return decode_response(hit, "openai")
            except Exception:
                return self._original.chat.completions.create(**kwargs)
        response = self._original.chat.completions.create(**kwargs)
        try:
            self._cache_manager.set(
                self._cache_request(kwargs), encode_response(response, "openai"),
                self._tenant_id,
            )
        except Exception as exc:
            logger.warning("SLMOpenAIAdapter: failed to cache response: %s", exc)
        return response

    async def _chat_create_async(self, **kwargs: Any) -> Any:
        if not self._config.cache_enabled or not self._should_cache(kwargs):
            return await self._original.chat.completions.create(**kwargs)
        key = self._build_cache_key(kwargs)
        if key is None:
            return await self._original.chat.completions.create(**kwargs)
        try:
            cached = self._cache_manager.get(self._cache_request(kwargs), self._tenant_id)
            hit = cached.data if cached is not None and cached.hit else None
        except Exception as exc:
            logger.warning("SLM cache read failed: %s", exc)
            hit = None
        if hit is not None:
            try:
                return decode_response(hit, "openai")
            except Exception:
                return await self._original.chat.completions.create(**kwargs)
        response = await self._original.chat.completions.create(**kwargs)
        try:
            self._cache_manager.set(
                self._cache_request(kwargs), encode_response(response, "openai"),
                self._tenant_id,
            )
        except Exception as exc:
            logger.warning("SLMOpenAIAdapter: failed to cache response: %s", exc)
        return response


class _ChatCompletionsProxy:
    """Mimics openai.resources.chat.Chat interface: `client.chat.completions.create`."""

    def __init__(self, adapter: SLMOpenAIAdapter) -> None:
        self._adapter = adapter
        self.completions = self._Completions(adapter)

    class _Completions:
        def __init__(self, adapter: SLMOpenAIAdapter) -> None:
            self._adapter = adapter

        def create(self, **kwargs: Any) -> Any:
            if self._adapter._is_async:
                return self._adapter._chat_create_async(**kwargs)
            return self._adapter._chat_create_sync(**kwargs)
