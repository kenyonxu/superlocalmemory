"""A real Agents SDK model fixture; only model HTTP traffic is intercepted."""

BOOTSTRAP = """
import httpx
from openai import AsyncOpenAI
from agents import set_default_openai_client, set_tracing_disabled

def model_response(request):
    return httpx.Response(200, json={
        "id": "resp_fixture", "object": "response", "created_at": 1,
        "model": "gpt-4o-mini", "status": "completed", "parallel_tool_calls": False,
        "tool_choice": "auto", "tools": [],
        "output": [{"type": "message", "id": "msg_fixture", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text",
                    "text": "Paris", "annotations": []}]}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
    })
set_default_openai_client(AsyncOpenAI(api_key="fixture", http_client=httpx.AsyncClient(
    transport=httpx.MockTransport(model_response))))
set_tracing_disabled(True)
"""
