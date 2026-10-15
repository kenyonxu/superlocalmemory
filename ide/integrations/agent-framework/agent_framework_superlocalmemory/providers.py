#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""SuperLocalMemory V3 - Microsoft Agent Framework providers.

Two integration points, backed by a local SuperLocalMemory data root:

* :class:`SuperLocalMemoryHistoryProvider` — persists a session's conversation
  through the Agent Framework ``HistoryProvider`` contract
  (``get_messages`` / ``save_messages``).
* :class:`SuperLocalMemoryContextProvider` — a ``ContextProvider`` that injects
  recent session memory as instructions before each run and persists the turn
  after each run.

Scope / status
--------------
Written against Microsoft Agent Framework (``agent-framework-core``) GA, which
in the Python 1.5.0 release replaced the beta ``invoking``/``invoked`` hooks
with ``before_run``/``after_run``. Message and session objects are converted
defensively because the framework's ``Message`` field layout is not fully
pinned in docs; validate in CI against the installed framework. All persistence
delegates to the framework-free :class:`V3MessageStore` (independently tested).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from agent_framework import (
    AgentSession,
    ContextProvider,
    HistoryProvider,
    Message,
    SessionContext,
)


def _data_root() -> Path:
    import os

    value = (
        os.environ.get("SLM_DATA_DIR")
        or os.environ.get("SL_MEMORY_PATH")
        or os.environ.get("SLM_HOME")
    )
    return Path(value).expanduser() if value else Path.home() / ".superlocalmemory"


def _session_id(session: Any) -> Optional[str]:
    for attr in ("id", "session_id", "thread_id"):
        value = getattr(session, attr, None)
        if value:
            return str(value)
    return None


# Native serialization contract opened 2026-10-11:
# https://raw.githubusercontent.com/microsoft/agent-framework/main/python/packages/core/agent_framework/_types.py

def _message_to_dict(message: Message) -> dict:
    """Preserve the SDK's full structured message, excluding raw provider objects."""
    return message.to_dict()


def _dict_to_message(data: dict) -> Message:
    """Restore native messages and the adapter's older role/text envelopes."""
    if "contents" in data:
        return Message.from_dict(data)
    return Message(role=data.get("role", "user"), contents=[data.get("text", "")])


def _open_store(db_path: Optional[str]):
    from agent_framework_superlocalmemory._v3_message_store import V3MessageStore

    store_path = Path(db_path) if db_path else _data_root() / "memory.db"
    return V3MessageStore(store_path)


class SuperLocalMemoryHistoryProvider(HistoryProvider):
    """Agent Framework conversation history backed by SuperLocalMemory V3."""

    def __init__(
        self,
        source_id: str = "slm-history",
        *,
        db_path: Optional[str] = None,
        load_messages: bool = True,
    ) -> None:
        super().__init__(source_id, load_messages=load_messages)
        self._store = _open_store(db_path)

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        return [_dict_to_message(d) for d in self._store.messages(session_id)]

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        for message in messages:
            self._store.append(session_id, _message_to_dict(message))

    def clear(self, session_id: str | None) -> None:
        self._store.clear(session_id)

    def close(self) -> None:
        self._store.close()


class SuperLocalMemoryContextProvider(ContextProvider):
    """Agent Framework ContextProvider backed by SuperLocalMemory V3.

    ``before_run`` injects recent session memory as instructions; ``after_run``
    persists the turn's input messages and the agent response.
    """

    def __init__(
        self,
        source_id: str = "slm-memory",
        *,
        db_path: Optional[str] = None,
        max_recall: int = 10,
    ) -> None:
        super().__init__(source_id)
        self._store = _open_store(db_path)
        self._max_recall = max_recall

    async def before_run(
        self,
        *,
        agent: Any,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        prior = self._store.messages(_session_id(session), limit=self._max_recall)
        if not prior:
            return
        messages = [_dict_to_message(m) for m in prior]
        lines = [f"{m.role}: {m.text}" for m in messages]
        context.extend_instructions(
            self.source_id,
            "Relevant prior memory from SuperLocalMemory:\n" + "\n".join(lines),
        )

    async def after_run(
        self,
        *,
        agent: Any,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        session_id = _session_id(session)
        for message in getattr(context, "input_messages", None) or []:
            self._store.append(session_id, _message_to_dict(message))
        response = getattr(context, "response", None)
        if response is not None:
            self._store.append(
                session_id, {"role": "assistant", "text": _response_text(response)}
            )

    def close(self) -> None:
        self._store.close()


def _response_text(response: Any) -> str:
    for attr in ("text", "content", "output_text"):
        value = getattr(response, attr, None)
        if value:
            return str(value)
    return str(response)
