# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""MCP expand drills into one result through the fetch gate; search has tiers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from superlocalmemory.mcp import tools_core
from superlocalmemory.retrieval import support_tiers as st


def test_search_tiers_put_supported_rows_first_and_cap_related() -> None:
    rows = [{"fact_id": "a", "content": "Hub v0.3.4 fixed the resultType bug."},
            {"fact_id": "b", "content": "release 4.1.25 adds scoring"},
            {"fact_id": "c", "content": "v0.3.4 tag"}]
    items, info = tools_core._search_tiers("What did hub v0.3.4 fix?", rows, filtered=False)
    assert items[0]["fact_id"] == "a" and items[0]["tier"] == st.SUPPORTED
    assert info["tier"] == st.SUPPORTED
    items, info = tools_core._search_tiers("v0.3.4 snazzle", rows, filtered=False)
    assert all(i["tier"] == st.RELATED for i in items) and len(items) <= st.RELATED_MAX
    assert info["abstention_note"] == st.RELATED_NOTE
    items, info = tools_core._search_tiers("zxqv", [], filtered=False)
    assert items == [] and info["abstention_note"] == tools_core.SEARCH_NO_MATCH
    assert len(tools_core._search_tiers("q", rows, filtered=True)[0]) == 3
    # Keyword search needs no model, so it is never warming up; the fields are
    # there so every recall door has the same envelope.
    assert info["warming_up"] is False and info["status_note"] == ""


class _Server:
    def __init__(self) -> None:
        self.captured: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.captured[fn.__name__] = fn
            return fn
        return deco


def _fact(fid, mid):
    return SimpleNamespace(fact_id=fid, memory_id=mid, content=f"content {fid}",
                           fact_type=SimpleNamespace(value="semantic"),
                           canonical_entities=[], created_at="2026-10-01")


def test_expand_uses_the_fetch_gate_and_accepts_a_memory_id(monkeypatch) -> None:
    facts = {"f1": _fact("f1", "m1"), "f2": _fact("f2", "m1")}
    asked: list = []

    def get_facts_by_ids(ids, pid):
        asked.append((tuple(ids), pid))
        return [facts[i] for i in ids if i in facts]

    db = SimpleNamespace(get_facts_by_ids=get_facts_by_ids,
                         execute=lambda sql, params=(): [{"fact_id": "f1"}, {"fact_id": "f2"}])
    engine = SimpleNamespace(_db=db, profile_id="default")
    server = _Server()

    async def _call_profile(get_engine, profile_id):
        return "default", None
    monkeypatch.setattr(tools_core, "_call_profile", _call_profile)
    from superlocalmemory.mcp import tools_expand

    monkeypatch.setattr(tools_expand, "visible_facts", lambda db, pid, found: list(found))
    tools_core.register_core_tools(server, lambda: engine)  # registers expand too
    out = asyncio.run(server.captured["expand"]("m1", depth=1))
    assert out["success"] and [r["fact_id"] for r in out["results"]] == ["f1", "f2"]
    assert asked[0] == (("m1",), "default")  # tried as a fact id first, in the profile
    hidden = SimpleNamespace(get_facts_by_ids=get_facts_by_ids, execute=lambda *a: [])
    monkeypatch.setattr(tools_expand, "visible_facts", lambda db, pid, found: [])
    engine._db = hidden
    assert not asyncio.run(server.captured["expand"]("f1", depth=1))["success"]
    assert asyncio.run(server.captured["expand"]("f1", depth=5))["code"] == "INVALID_LAYERS"


def test_expand_lives_in_its_own_module_and_is_admitted_as_a_recall() -> None:
    import inspect

    from superlocalmemory.mcp import tools_expand

    assert "async def expand" not in inspect.getsource(tools_core)
    assert "@admits(OperationKind.RECALL)" in inspect.getsource(tools_expand)
