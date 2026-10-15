"""Real tool and resource handlers hide an erasure-in-progress fact."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from superlocalmemory.core.delete_refusal import _drop_stale_erasure
from tests.test_retrieval.test_erasure_visibility import (  # noqa: F401
    CONTROL,
    HIDDEN,
    tombstone,
)
from tests.test_retrieval.test_erasure_visibility import (
    store as store,
)


class CapturingServer:
    def __init__(self):
        self.handlers = {}

    def tool(self, *args, **kwargs):
        def decorate(fn):
            self.handlers[fn.__name__] = fn
            return fn
        return decorate

    def resource(self, uri):
        def decorate(fn):
            self.handlers[uri] = fn
            return fn
        return decorate


@pytest.mark.parametrize("surface", ["fetch", "search", "list_recent", "slm://recent",
                                      "slm://engagement"])
def test_mcp_surfaces_recheck_tombstone_and_heal(store, monkeypatch, surface):
    from superlocalmemory.mcp import resources, tools_core

    engine = SimpleNamespace(_db=store, profile_id="default")
    async def profile(*args, **kwargs):
        return "default"
    monkeypatch.setattr(tools_core, "_runtime_profile", profile)
    server = CapturingServer()
    tools_core.register_core_tools(server, lambda: engine)
    resources.register_resources(server, lambda: engine)
    store.execute("UPDATE atomic_facts SET access_count=0")
    store.execute("UPDATE atomic_facts SET access_count=5 WHERE fact_id IN (?,?)",
                  (HIDDEN, CONTROL))
    # The resource uses previews, so put distinguishable markers at their start.
    store.execute("UPDATE atomic_facts SET content='erasuremarker memory release' "
                  "WHERE fact_id=?", (HIDDEN,))
    store.execute("UPDATE atomic_facts SET content='controlmarker memory release' "
                  "WHERE fact_id=?", (CONTROL,))
    args = {"fetch": ([HIDDEN, CONTROL],), "search": ("memory", 30),
            "list_recent": (30,)}.get(surface, ())
    def read():
        return str(asyncio.run(server.handlers[surface](*args)))
    before = read()
    assert "erasuremarker" in before and "controlmarker" in before, before
    tombstone(store)
    during = read()
    assert "erasuremarker" not in during and "controlmarker" in during, during
    assert _drop_stale_erasure(store, "default", HIDDEN)
    after = read()
    assert "erasuremarker" in after and "controlmarker" in after
