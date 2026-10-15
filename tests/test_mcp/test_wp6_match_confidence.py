"""The query match signal must survive both MCP recall surfaces and typed adapter."""

import asyncio

import pytest

from .test_recall_metadata_reaches_mcp import _FakePool, _tool

PAYLOAD = {
    "ok": True,
    "results": [
        {
            "fact_id": "f1",
            "content": "A synthetic fact.",
            "score": 0.7,
            "confidence": 0.99,
            "match_confidence": 0.42,
            "confidence_basis": "raw_semantic_evidence",
        }
    ],
    "result_count": 1,
}


@pytest.mark.parametrize("name", ["recall", "recall_trace"])
def test_mcp_keeps_query_match_separate_from_stored_assertion_confidence(name, monkeypatch):
    result = asyncio.run(_tool(name, monkeypatch, PAYLOAD)(query="a question?"))["results"][0]
    assert result["match_confidence"] == 0.42
    assert result["confidence_basis"] == "raw_semantic_evidence"
    assert result["confidence"] == 0.99


def test_pool_adapter_carries_the_query_match_signal(monkeypatch):
    from superlocalmemory.mcp import _pool_adapter

    monkeypatch.setattr(_pool_adapter, "_pool", lambda: _FakePool(PAYLOAD))
    item = _pool_adapter.pool_recall("a question?").results[0]
    assert item.match_confidence == 0.42
    assert item.confidence_basis == "raw_semantic_evidence"


@pytest.mark.parametrize("name", ["recall", "recall_trace"])
def test_unjudged_fallback_with_no_semantic_score_still_reaches_mcp(name, monkeypatch):
    payload = {
        **PAYLOAD,
        "results": [
            {
                **PAYLOAD["results"][0],
                "score": None,
                "match_confidence": None,
                "confidence_basis": "unmeasured",
            }
        ],
        "answer_check_status": "skipped",
        "answer_check_detail": "budget",
    }
    out = asyncio.run(_tool(name, monkeypatch, payload)(query="a question?"))
    assert out["success"] is True
    assert out["answerability"] == "unjudged"
    assert out["results"][0]["match_confidence"] is None
