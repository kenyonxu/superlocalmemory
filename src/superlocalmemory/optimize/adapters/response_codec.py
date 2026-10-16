"""Versioned JSON codec for native SDK responses; never deserialize arbitrary types."""

from __future__ import annotations

import json
from typing import Any

# Typed SDK responses are Pydantic models. Official API contracts opened 2026-10-11:
# https://github.com/openai/openai-python#using-types
# https://github.com/anthropics/anthropic-sdk-python
_VERSION_KEY = "_slm_sdk_response"


def encode_response(response: Any, provider: str) -> dict:
    if isinstance(response, dict):
        return dict(response)
    payload = response.model_dump(mode="json")
    if not isinstance(payload, dict):
        raise TypeError("SDK response must serialize to a JSON object")
    return {**payload, _VERSION_KEY: {"version": 1, "provider": provider}}


def decode_response(encoded: bytes, provider: str) -> Any:
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise TypeError("Cached SDK response must be a JSON object")
    marker = payload.pop(_VERSION_KEY, None)
    if marker is None:
        return payload  # backwards-compatible dict clients
    if marker != {"version": 1, "provider": provider}:
        raise ValueError("Unsupported cached SDK response version or provider")
    if provider == "openai":
        from openai.types.chat import ChatCompletion

        return ChatCompletion.model_validate(payload)
    if provider == "anthropic":
        from anthropic.types import Message

        return Message.model_validate(payload)
    raise ValueError("Unsupported SDK response provider")
