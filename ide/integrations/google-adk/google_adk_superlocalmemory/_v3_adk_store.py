# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""Framework-free session-event store over SuperLocalMemory's V3 engine.

Google ADK's ``BaseMemoryService`` persists full session events and provides
semantic search over them.  This module implements that data model on top of
``MemoryEngine`` with no Google ADK import, so the storage contract can be
exercised and verified on its own.

Storage mapping
---------------
Each individual session event is one SLM memory whose ``session_id``
deterministically encodes the app, user, session, and event index::

    adk:{app_name}\\x1f{user_id}\\x1f{session_id}\\x1e{event_index:08d}

Content is a JSON envelope that is always substantial (several fields) so
SLM's ingestion pipeline creates a queryable atomic-fact row even for very
short event text::

    {
        "adapter": "google-adk memory service",
        "app_name": "...", "user_id": "...", "session_id": "...",
        "event_index": 0,
        "event": {
            "text": "...", "author": "...",
            "timestamp_float": 1.0, "timestamp_iso": "..."
        },
        "created_at": "<iso>"
    }

Re-calling ``add_events`` for the same (app_name, user_id, session_id)
replaces ALL previously stored events for that session (idempotent upsert).

Reads
-----
``list_events_for_namespace`` performs a bounded LIKE prefix scan (all events
for one app/user).  ``search`` uses ``engine.recall`` for semantic ranking and
post-filters results by the app/user session-id prefix.

Security
--------
* All LIKE wildcards (``%``, ``_``, ``\\``) are escaped before use in SQL.
* Every LIKE scan is capped at ``_MAX_SCAN`` rows to prevent DoS via huge
  collections.
* Queries use parameterised placeholders; no string interpolation in SQL.
* Namespace component separators (``\\x1f``, ``\\x1e``) are non-printing chars
  unlikely to appear in real app names; ``_validate_component`` rejects them
  explicitly to prevent session-id collisions.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_CONTENT_BYTES = 1_000_000
_MAX_SCAN = 10_000   # hard cap on prefix scans (DoS guard)
_SID_PREFIX = "adk:"
_NS_SEP = "\x1f"     # unit separator — between app_name, user_id, session_id
_EVT_SEP = "\x1e"    # record separator — between session path and event index
_SEQ_WIDTH = 8       # zero-padded event index width


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so the value is treated as a literal prefix."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate_component(name: str, label: str) -> None:
    """Reject reserved separator chars that could collapse distinct session-ids."""
    for ch in (_NS_SEP, _EVT_SEP):
        if ch in name:
            raise ValueError(
                f"{label} must not contain reserved separator characters "
                f"(U+001F, U+001E)"
            )


def _session_prefix(app_name: str, user_id: str) -> str:
    """Prefix for all events belonging to one app+user."""
    return f"{_SID_PREFIX}{app_name}{_NS_SEP}{user_id}{_NS_SEP}"


def _full_session_prefix(app_name: str, user_id: str, session_id: str) -> str:
    """Prefix for all events belonging to one specific session."""
    return f"{_SID_PREFIX}{app_name}{_NS_SEP}{user_id}{_NS_SEP}{session_id}{_EVT_SEP}"


def _event_sid(app_name: str, user_id: str, session_id: str, idx: int) -> str:
    return (
        f"{_SID_PREFIX}{app_name}{_NS_SEP}{user_id}{_NS_SEP}"
        f"{session_id}{_EVT_SEP}{idx:0{_SEQ_WIDTH}d}"
    )


def _load_v3_types():
    try:
        from superlocalmemory.core.config import SLMConfig
        from superlocalmemory.core.engine import MemoryEngine
        from superlocalmemory.storage.models import Mode
    except ImportError as exc:  # pragma: no cover - defensive
        raise ImportError(
            "SuperLocalMemory V3 is not installed in this Python environment. "
            "Install SLM in its own environment and set SLM_ENGINE_PYTHON to its Python executable."
        ) from exc
    return SLMConfig, MemoryEngine, Mode


# ---------------------------------------------------------------------------
# Store core
# ---------------------------------------------------------------------------

def _event_envelope(app_name, user_id, session_id, idx, event, now):
    """Keep short event bodies queryable with the adapter's established envelope."""
    return {
        "adapter": "google-adk memory service", "app_name": app_name,
        "user_id": user_id, "session_id": session_id, "event_index": idx,
        "event": {"text": event.get("text", "") or "",
                  "author": event.get("author", "") or "unknown",
                  "timestamp_float": float(event.get("timestamp_float", 0.0)),
                  "timestamp_iso": event.get("timestamp_iso", now)},
        "created_at": now,
    }


class V3ADKStore:
    """Session-event storage backed by a SuperLocalMemory V3 engine.

    This class is framework-free: it operates on plain Python dicts and does
    not import any Google ADK types.  The ``memory_service.py`` wrapper handles
    all ADK-typed conversions.
    """

    def __new__(cls, db_path):
        import os

        python = os.environ.get("SLM_ENGINE_PYTHON")
        if python:
            import importlib.util

            bridge = Path(__file__).with_name("_engine_bridge.py")
            if not bridge.exists():  # source checkout; wheel bundles a regular file
                bridge = Path(__file__).resolve().parents[2] / "_engine_bridge.py"
            spec = importlib.util.spec_from_file_location("_slm_engine_bridge", bridge)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.RemoteStore(python, __file__, cls.__name__, db_path)
        return super().__new__(cls)

    def __init__(self, db_path: str | Path) -> None:
        SLMConfig, MemoryEngine, Mode = _load_v3_types()
        path = Path(db_path).expanduser().resolve()
        config = SLMConfig.for_mode(Mode.A, base_dir=path.parent)
        config.db_path = path
        config.forgetting = replace(config.forgetting, enabled=False)
        config.retrieval.use_cross_encoder = False
        self._engine = MemoryEngine(config)
        self._engine.initialize()

    # -- writes -------------------------------------------------------------

    def add_events(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        events: list[dict[str, Any]],
    ) -> None:
        """Replace a session's stored events; an empty list clears the session."""
        _validate_component(app_name, "app_name")
        _validate_component(user_id, "user_id")
        _validate_component(session_id, "session_id")

        # Idempotent replace: clear previous events for this session.
        self._delete_prefix(_full_session_prefix(app_name, user_id, session_id))

        now = _now_iso()
        for idx, event in enumerate(events):
            envelope = _event_envelope(app_name, user_id, session_id, idx, event, now)
            content = json.dumps(envelope, ensure_ascii=False)
            if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
                raise ValueError(
                    f"event at index {idx} exceeds maximum size of "
                    f"{MAX_CONTENT_BYTES} bytes"
                )
            self._engine.store(
                content,
                session_id=_event_sid(app_name, user_id, session_id, idx),
                metadata={
                    "integration": "google-adk",
                    "adk_app_name": app_name,
                    "adk_user_id": user_id,
                    "adk_session_id": session_id,
                    "tags": ["google-adk"],
                    "importance": 3,
                    "project_name": "google-adk",
                },
            )

    # -- reads (semantic) ---------------------------------------------------

    def search(
        self,
        app_name: str,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Recall within a namespace; use an ordered scan when recall has no matches."""
        _validate_component(app_name, "app_name")
        _validate_component(user_id, "user_id")
        limit = min(int(limit), _MAX_SCAN)
        prefix = _session_prefix(app_name, user_id)

        # -- Semantic path (recall + post-filter) ---------------------------
        try:
            response = self._engine.recall(query, limit=limit * 4, fast=True)
            results = []
            seen_sids: set[str] = set()
            for r in getattr(response, "results", []):
                fact = getattr(r, "fact", None)
                sid = getattr(fact, "session_id", None) or ""
                if not sid.startswith(prefix):
                    continue
                if sid in seen_sids:
                    continue
                seen_sids.add(sid)
                rows = self._engine.db.execute(
                    "SELECT content FROM memories "
                    "WHERE profile_id=? AND session_id=? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (self._engine.profile_id, sid),
                )
                for row in rows:
                    env = self._parse(dict(row).get("content", ""))
                    if env is not None:
                        results.append(env)
                if len(results) >= limit:
                    break
            if results:
                return results[:limit]
        except Exception:  # noqa: BLE001 — recall pipeline optional
            pass

        # -- DB fallback (ordered by insertion time) ------------------------
        return self.list_events_for_namespace(app_name, user_id, limit=limit)

    # -- reads (DB scan) ----------------------------------------------------

    def list_events_for_namespace(
        self,
        app_name: str,
        user_id: str,
        limit: int = _MAX_SCAN,
    ) -> list[dict[str, Any]]:
        """Return all stored events for an app+user namespace, oldest first."""
        _validate_component(app_name, "app_name")
        _validate_component(user_id, "user_id")
        limit = min(int(limit), _MAX_SCAN)
        prefix = _session_prefix(app_name, user_id)
        escaped = _escape_like(prefix)
        rows = self._engine.db.execute(
            "SELECT content FROM memories "
            "WHERE profile_id=? AND session_id LIKE ? ESCAPE '\\' "
            "ORDER BY created_at ASC, rowid ASC LIMIT ?",
            (self._engine.profile_id, escaped + "%", limit),
        )
        out = []
        for row in rows:
            env = self._parse(dict(row).get("content", ""))
            if env is not None:
                out.append(env)
        return out

    def list_events_for_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return all stored events for a specific session, ordered by index."""
        _validate_component(app_name, "app_name")
        _validate_component(user_id, "user_id")
        _validate_component(session_id, "session_id")
        prefix = _full_session_prefix(app_name, user_id, session_id)
        escaped = _escape_like(prefix)
        rows = self._engine.db.execute(
            "SELECT content FROM memories "
            "WHERE profile_id=? AND session_id LIKE ? ESCAPE '\\' "
            "ORDER BY session_id ASC, rowid ASC LIMIT ?",
            (self._engine.profile_id, escaped + "%", _MAX_SCAN),
        )
        out = []
        for row in rows:
            env = self._parse(dict(row).get("content", ""))
            if env is not None:
                out.append(env)
        return out

    def close(self) -> None:
        self._engine.close()

    # -- internals ----------------------------------------------------------

    def _delete_prefix(self, prefix: str) -> None:
        """Delete all memories whose session_id starts with ``prefix``."""
        escaped = _escape_like(prefix)
        facts = self._engine.db.execute(
            "SELECT af.fact_id FROM atomic_facts af JOIN memories m "
            "ON af.memory_id = m.memory_id "
            "WHERE m.profile_id=? AND m.session_id LIKE ? ESCAPE '\\' LIMIT ?",
            (self._engine.profile_id, escaped + "%", _MAX_SCAN),
        )
        fact_ids = [str(dict(r)["fact_id"]) for r in facts]
        with self._engine.db.transaction():
            self._engine.db.execute(
                "DELETE FROM memories "
                "WHERE profile_id=? AND session_id LIKE ? ESCAPE '\\'",
                (self._engine.profile_id, escaped + "%"),
            )
        self._remove_external_indexes(fact_ids)

    @staticmethod
    def _parse(content: str) -> dict | None:
        try:
            data = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or "adapter" not in data or "event" not in data:
            return None
        return data

    def _remove_external_indexes(self, fact_ids: list[str]) -> None:
        ann = getattr(self._engine, "_ann_index", None)
        vector = getattr(self._engine, "_vector_store", None)
        for fact_id in fact_ids:
            if ann is not None:
                try:
                    ann.remove(fact_id)
                except Exception:
                    pass
            if vector is not None and getattr(vector, "available", False):
                try:
                    vector.delete(fact_id)
                except Exception:
                    pass
