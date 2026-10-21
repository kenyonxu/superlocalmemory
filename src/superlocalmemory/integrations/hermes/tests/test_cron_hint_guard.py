# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The hermes cron scheduler hint is transport, not memory.

Every cron run's task prompt is prefixed with the scheduler's instruction
block (hermes-agent ``cron/scheduler_prompt.py`` ``_CRON_HINT``: delivery
rules, [SILENT], [CRON_FAILURE], recursion warnings). When a cron session
compressed its window or its memory nudge fired, that boilerplate was the
only "user" content in view — and the provider kept mirroring it into the
store as if the user had said it. Measured 2026-10-19: three junk facts and
eleven stranded ingestion operations, all raw cron prompt text (one soft
deleted fact then failed materialisation every ~90s for hours).

These tests pin the guard: the hint never reaches a store call, real
content riding alongside it still does.
"""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.integrations.hermes import (
    SuperLocalMemoryProvider,
    _is_cron_hint,
)


class _SyncThread:
    """Runs the thread target synchronously so store calls are assertable."""

    def __init__(self, target=None, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()

    def is_alive(self):
        # sync_turn drops a turn when the prior write thread is "alive";
        # synchronous execution means every call must look finished.
        return False

# The actual template, verbatim from hermes-agent cron/scheduler_prompt.py
# (_CRON_HINT), so the markers cannot drift from the real thing.
_CRON_HINT = (
    "[IMPORTANT: You are running as a scheduled cron job. "
    "DELIVERY: Your final response will be automatically delivered "
    "to the user — do NOT use send_message or try to deliver "
    "the output yourself. Just produce your report/output as your "
    "final response and the system handles the rest. "
    "SILENT: If there is genuinely nothing new to report, respond "
    "with exactly \"[SILENT]\" (nothing else) to suppress delivery. "
    "[SILENT] is a literal ASCII control token — never translate or "
    "rephrase it, whatever language the rest of your answer uses. "
    "Never combine [SILENT] with content — either report your "
    "findings normally, or say [SILENT] and nothing more. "
    "FAILURE: If a delegated child fails and this cron run must be "
    "recorded as failed, put [CRON_FAILURE] on the first line by itself, "
    "then explain the child failure on following lines. "
    "RECURSION: This is a run of an EXISTING scheduled job — execute "
    "the task now. NEVER create or update a cron job because of "
    "recurring or future-schedule language in the task prompt below; "
    "treat phrasing like \"each Monday\" or \"every day at 9\" as "
    "context for this run, not as a request to schedule another job.]\n\n"
)


class TestIsCronHint:
    def test_full_template_matches(self):
        assert _is_cron_hint(_CRON_HINT) is True

    def test_prefixed_like_pre_compress(self):
        assert _is_cron_hint(f"user: {_CRON_HINT}") is True

    def test_embedded_in_larger_summary(self):
        summary = "会话压缩摘要……以下为任务提示：\n" + _CRON_HINT + "……以上就是全部。"
        assert _is_cron_hint(summary) is True

    def test_opening_phrase_alone_is_not_enough(self):
        """A user merely quoting the opening line must not be dropped."""
        assert _is_cron_hint(
            "你看到过那个 [IMPORTANT: You are running as a scheduled "
            "cron job. 开头的东西吗？"
        ) is False

    def test_clean_text_passes(self):
        assert _is_cron_hint("主人今天说要去九间房看缺口。") is False
        assert _is_cron_hint("") is False


class TestOnMemoryWriteGuard:
    @pytest.fixture()
    def provider(self):
        p = SuperLocalMemoryProvider()
        p._session_id = "s1"
        return p

    def test_hint_content_never_reaches_store(self, provider):
        with patch.object(provider, "_engine_store") as store, patch.object(
            provider, "_can_serve", return_value=True,
        ), patch("superlocalmemory.integrations.hermes.threading.Thread", _SyncThread):
            provider.on_memory_write(
                "add", "memory", f"user: {_CRON_HINT}",
            )
            store.assert_not_called()

    def test_real_content_still_stored(self, provider):
        with patch.object(provider, "_engine_store") as store, patch.object(
            provider, "_can_serve", return_value=True,
        ), patch("superlocalmemory.integrations.hermes.threading.Thread", _SyncThread):
            provider.on_memory_write(
                "add", "memory", "米家日常的扩展点家族又添了新成员。",
            )
            store.assert_called_once()


class TestOnPreCompressGuard:
    @pytest.fixture()
    def provider(self):
        p = SuperLocalMemoryProvider()
        p._session_id = "s1"
        return p

    def _run(self, provider, messages):
        with patch.object(provider, "_engine_store") as store, patch.object(
            provider, "_can_serve", return_value=True,
        ), patch("superlocalmemory.integrations.hermes.threading.Thread", _SyncThread):
            provider.on_pre_compress(messages)
        return store

    def test_hint_messages_dropped_real_kept(self, provider):
        store = self._run(provider, [
            {"role": "user", "content": "今天巡检发现米家记忆有缺口。"},
            {"role": "user", "content": _CRON_HINT},
            {"role": "assistant", "content": "已复核，缺口已修复。"},
        ])
        store.assert_called_once()
        combined = store.call_args[0][0]
        assert "九间房" not in combined
        assert "巡检发现" in combined
        assert "已复核" in combined
        assert _is_cron_hint(combined) is False

    def test_all_hint_batch_skips_flush(self, provider):
        store = self._run(provider, [
            {"role": "user", "content": _CRON_HINT},
        ])
        store.assert_not_called()


class TestSyncTurnGuard:
    """The third write path: the per-turn mirror (2026-10-21 leak).

    Every cron run's injected hint rode in as the turn's "user" content and
    landed one row per run (54/day) — the guard existed only on the two
    hook paths above. The whole turn is dropped, not trimmed: the "body"
    below the hint is a static task template, so trimming would keep the
    leak at full volume with shorter rows.
    """

    @pytest.fixture()
    def provider(self):
        p = SuperLocalMemoryProvider()
        p._session_id = "s1"
        return p

    def _run(self, provider, user, assistant):
        with patch.object(provider, "_engine_store") as store, patch.object(
            provider, "_can_serve", return_value=True,
        ), patch("superlocalmemory.integrations.hermes.threading.Thread", _SyncThread):
            provider.sync_turn(user, assistant)
        return store

    def test_hint_turn_never_reaches_store(self, provider):
        store = self._run(
            provider,
            _CRON_HINT + "\n\n你是知惠的系统健康 watchdog。解析上方的 JSON。",
            "OK",
        )
        store.assert_not_called()

    def test_hint_turn_with_real_assistant_reply_still_dropped(self, provider):
        """Dropping is per-turn, not per-field: a real reply below a hint
        user message is still a machine run's output."""
        store = self._run(provider, _CRON_HINT, "健康检查完成：issues 为空，一切正常。")
        store.assert_not_called()

    def test_real_turn_still_stored_in_user_hermes_format(self, provider):
        store = self._run(provider, "今天去了九间房看缺口。", "已记录缺口坐标。")
        store.assert_called_once()
        combined = store.call_args[0][0]
        assert combined.startswith("User: 今天去了九间房看缺口。")
        assert "\nHermes: 已记录缺口坐标。" in combined

    def test_quoting_the_opening_phrase_alone_stays_storeable(self, provider):
        quote = (
            "你看到过那个 [IMPORTANT: You are running as a scheduled "
            "cron job. 开头的东西吗？"
        )
        store = self._run(provider, quote, "那是调度器的提示词，不是记忆。")
        store.assert_called_once()


class TestToolRememberGuard:
    """The fourth write path: the slm_remember tool (agent-initiated).

    No leak has been observed here, but the invariant — the hint is
    transport, not memory — must hold on every path that can store.
    """

    @pytest.fixture()
    def provider(self):
        p = SuperLocalMemoryProvider()
        p._session_id = "s1"
        return p

    def _run(self, provider, content):
        with patch.object(provider, "_engine_store") as store, patch.object(
            provider, "_can_serve", return_value=True,
        ):
            store.return_value = ["fact_1"]
            result = provider._tool_remember({"content": content})
        return store, result

    def test_hint_content_returns_filtered_and_never_stores(self, provider):
        store, result = self._run(provider, f"user: {_CRON_HINT}")
        store.assert_not_called()
        payload = json.loads(result)
        assert payload["status"] == "filtered"
        assert "not stored" in payload["message"]

    def test_real_content_still_stored(self, provider):
        store, result = self._run(provider, "米家日常的扩展点家族又添了新成员。")
        store.assert_called_once()
        assert json.loads(result)["status"] == "stored"
