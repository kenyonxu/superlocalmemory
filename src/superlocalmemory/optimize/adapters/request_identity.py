"""SDK cache identity includes the provider origin without persisting its URL."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def cache_request(client: Any, provider: str, kwargs: dict) -> dict:
    exclude = {"model", "messages", "system", "stream", "user", "metadata", "request_id"}
    origin = json.dumps(
        {"sdk": provider, "base_url": str(getattr(client, "base_url", ""))}, sort_keys=True
    )
    origin_hash = hashlib.sha256(origin.encode("utf-8")).hexdigest()
    # The canonical key builder hashes the system identity. Namespace SDK
    # responses separately from proxy/dict responses and other provider URLs;
    # retain the real model name for canonical model-tag invalidation.
    system = json.dumps(
        {"sdk_origin": origin_hash, "system": kwargs.get("system", "")}, sort_keys=True
    )
    return {
        "model": kwargs.get("model", "unknown"),
        "messages": kwargs.get("messages", []),
        "system": system,
        "params": {k: v for k, v in kwargs.items() if k not in exclude},
    }
