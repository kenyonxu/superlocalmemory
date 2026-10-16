# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""4.1.25: two settings that did nothing now do what they say.

``auto_recall.on_every_prompt`` turns the per-prompt recall hooks on or off
(on by default: those hooks always ran). ``auto_capture.capture_session_summary``
turns the end-of-session summary on or off. Turning auto-recall or
auto-capture off as a whole turns these off too.

Until now every save from the dashboard wrote ``on_every_prompt: false`` (the
old, unused default), so a store without ``rules_version`` 2 drops that one
stored value instead of silently ending per-prompt recall.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from superlocalmemory.hooks import hook_handlers, rules_engine
from superlocalmemory.hooks.rules_engine import RulesEngine


def _write(path, rules, version=None):
    data = {"mode": "a", "rules": rules}
    if version is not None:
        data["rules_version"] = version
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_both_are_on_by_default() -> None:
    rules = RulesEngine()
    assert rules.per_prompt_recall_on() and rules.captures_session_summary()


def test_a_stored_false_from_before_the_key_was_wired_is_dropped(tmp_path) -> None:
    path = _write(tmp_path / "config.json", {
        "auto_recall": {"enabled": True, "on_every_prompt": False, "max_memories_injected": 7},
        "auto_capture": {"capture_session_summary": True}})
    rules = RulesEngine(config_path=path)
    assert rules.per_prompt_recall_on()
    assert rules.get_recall_config()["max_memories_injected"] == 7  # the rest is kept
    rules.update_rule("auto_recall", "on_every_prompt", False)
    rules.save(path)
    saved = json.loads(path.read_text())
    assert saved["rules_version"] == rules_engine.RULES_VERSION and saved["mode"] == "a"
    assert not RulesEngine(config_path=path).per_prompt_recall_on()  # a real choice holds


def test_turning_a_whole_section_off_turns_its_hook_off(tmp_path) -> None:
    assert not RulesEngine(config={"auto_recall": {"enabled": False}}).per_prompt_recall_on()
    assert not RulesEngine(config={"auto_capture": {"enabled": False}}).captures_session_summary()
    assert not RulesEngine(
        config={"auto_capture": {"capture_session_summary": False}}).captures_session_summary()


def test_an_unreadable_config_means_the_defaults(tmp_path) -> None:
    bad = tmp_path / "config.json"
    bad.write_text("{not json", encoding="utf-8")
    assert rules_engine.load(bad).per_prompt_recall_on()
    assert rules_engine.load(tmp_path / "missing.json").captures_session_summary()


@pytest.fixture
def config_with(monkeypatch, tmp_path):
    def _set(**sections):
        path = _write(tmp_path / "config.json", sections, rules_engine.RULES_VERSION)
        monkeypatch.setattr(rules_engine, "_config_path", lambda: path)
        return path
    return _set


PER_PROMPT = ["auto_recall", "user_prompt_submit", "user_prompt_rehash", "topic_shift",
              "codex-prompt"]


@pytest.mark.parametrize("action", PER_PROMPT)
def test_per_prompt_hooks_answer_empty_when_switched_off(action, config_with, monkeypatch,
                                                         capsys) -> None:
    from superlocalmemory.hooks import (
        auto_recall_hook,
        topic_shift_hook,
        user_prompt_hook,
        user_prompt_rehash_hook,
    )

    config_with(auto_recall={"on_every_prompt": False})
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"prompt": "what db", "session_id": "s"}'))

    def ran():
        pytest.fail("ran while switched off")

    monkeypatch.setattr(hook_handlers, "_hook_codex_prompt", ran)
    for module in (auto_recall_hook, topic_shift_hook, user_prompt_hook,
                   user_prompt_rehash_hook):
        monkeypatch.setattr(module, "main", ran)
    with pytest.raises(SystemExit) as done:
        hook_handlers.handle_hook(action)
    assert done.value.code == 0 and capsys.readouterr().out == "{}"


def test_the_recall_hook_runs_when_on(config_with, monkeypatch) -> None:
    from superlocalmemory.hooks import auto_recall_hook

    config_with(auto_recall={"on_every_prompt": True})
    ran = []
    monkeypatch.setattr(auto_recall_hook, "main", lambda: ran.append(1) or 0)
    with pytest.raises(SystemExit):
        hook_handlers.handle_hook("auto_recall")
    assert ran == [1]


def test_the_recall_hook_module_entry_respects_it_too(config_with, monkeypatch,
                                                       capsys) -> None:
    from superlocalmemory.hooks import auto_recall_hook

    config_with(auto_recall={"enabled": False})
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"prompt": "which database do we use"}'))
    monkeypatch.setattr(auto_recall_hook, "_try_socket_first",
                        lambda *a: pytest.fail("recalled while switched off"))
    assert auto_recall_hook.main() == 0 and capsys.readouterr().out == "{}"


@pytest.mark.parametrize("on", [True, False])
def test_the_session_summary_is_saved_only_when_on(on, config_with, monkeypatch) -> None:
    config_with(auto_capture={"capture_session_summary": on})
    posts = []
    monkeypatch.setattr(hook_handlers, "_daemon_post", lambda path, *a, **k: posts.append(path))
    monkeypatch.setattr(hook_handlers, "_run_quiet", lambda *a, **k: "")
    monkeypatch.setattr(hook_handlers, "_maybe_consolidate", lambda: None)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    with pytest.raises(SystemExit):
        hook_handlers.handle_hook("stop")
    assert ("/remember" in posts) is on


def test_the_hook_daemon_socket_respects_it_too(config_with, tmp_path) -> None:
    from types import SimpleNamespace

    from superlocalmemory.hooks.hook_daemon import HookDaemon

    asked = []
    daemon = HookDaemon(sock_path=tmp_path / "s.sock", queue_db_path=tmp_path / "q.db")
    daemon._queue = SimpleNamespace(enqueue=lambda **k: asked.append(k) or "r1",
                                    poll_result=lambda *a, **k: {"results": []})
    config_with(auto_recall={"on_every_prompt": False})
    assert daemon._process_request({"prompt": "which database do we use"}) == {}
    assert asked == []
    config_with(auto_recall={"on_every_prompt": True})
    daemon._process_request({"prompt": "which database do we use"})
    assert len(asked) == 1
