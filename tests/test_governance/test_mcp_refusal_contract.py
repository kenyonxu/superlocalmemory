"""Permission refusals are tool execution results with explicit role guidance."""

# ruff: noqa: F811
import json

from tests.test_governance.test_role_surface_matrix_mcp import (  # noqa: F401
    _call,
    http_client,
    store,
)


def test_permission_refusal_has_tool_result_and_required_roles(http_client):
    client, rbac, ids = http_client
    result = _call(
        client,
        "wp9_probe_write",
        headers={"X-SLM-User-Session": rbac.create_session(ids["viewer_a"])},
    )
    assert result["isError"] is True
    body = result["structuredContent"]
    assert body["code"] == "NOT_AUTHORIZED"
    assert body["retryable"] is False
    assert "member" in body["message"] and "admin" in body["message"]
    assert body["required_roles"] == ["admin", "member", "owner"]
    assert json.loads(result["content"][0]["text"]) == body
