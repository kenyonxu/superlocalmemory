# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``capture_turns: true`` keeps every completed Hermes turn (issue #263).

The plugin sent each turn to ``observe``, the auto-capture path that keeps only
English sentences about a decision, a bug or a preference. An ordinary turn,
and every turn in another language ("User: 你好 ..."), came back
``captured: false, reason: no patterns matched`` and was dropped without a
trace, though the README promised full user/assistant turn capture. Turns now
go through ``remember``, the ordinary save path, in any language.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugin-src" / "hermes"


def _plugin(capture: bool):
    spec = importlib.util.spec_from_file_location("slm_hermes_turns", PLUGIN / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    class Context:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call_mcp(self, _server, tool, arguments, timeout=0):
            self.calls.append((tool, arguments))
            return {"ok": True}

        def get_config(self, key, default):
            return capture if key == "capture_turns" else default

    context = Context()
    return module.SlmHermesPlugin(context), context


def test_an_ordinary_turn_is_remembered():
    plugin, ctx = _plugin(True)
    plugin.post_llm_call(session_id="s1", user_message="What's a good name for the garden app?",
                         assistant_response="Something short, like Sprout.")
    assert [name for name, _ in ctx.calls] == ["remember"]
    args = ctx.calls[0][1]
    assert "garden app" in args["content"] and "Sprout" in args["content"]
    assert args["agent_id"] == "hermes" and args["session_id"] == "s1"
    assert "hermes" in args["tags"]


def test_a_turn_in_any_language_is_remembered():
    plugin, ctx = _plugin(True)
    plugin.post_llm_call(session_id="s1", user_message="你好，我的项目叫星河。",
                         assistant_response="好的，我记住了：你的项目叫星河。")
    assert [name for name, _ in ctx.calls] == ["remember"]
    assert "星河" in ctx.calls[0][1]["content"]


def test_a_retried_turn_is_saved_once():
    plugin, ctx = _plugin(True)
    turn = dict(session_id="s1", user_message="Deploy window is Friday 6 pm.", assistant_response="Noted.")
    plugin.post_llm_call(**turn)
    plugin.post_llm_call(**turn)
    plugin.post_llm_call(**{**turn, "user_message": "Deploy window moved to Monday."})
    keys = [args["idempotency_key"] for _, args in ctx.calls]
    assert keys[0] == keys[1] != keys[2] and keys[0]


def test_secrets_never_reach_memory():
    plugin, ctx = _plugin(True)
    canary = "sk-live-DO_NOT_STORE_1234567890"
    plugin.post_llm_call(session_id="s1", user_message=f"my key is {canary}", assistant_response=canary)
    assert canary not in repr(ctx.calls)


def test_an_empty_turn_saves_nothing():
    plugin, ctx = _plugin(True)
    plugin.post_llm_call(session_id="s1", user_message="", assistant_response="  ")
    assert ctx.calls == []


def test_capture_stays_opt_in():
    plugin, ctx = _plugin(False)
    plugin.post_llm_call(session_id="s1", user_message="hello there friend", assistant_response="hi")
    assert ctx.calls == []
