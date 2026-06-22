"""SuperLocalMemoryProvider — MSLM Hermes MemoryProvider.

Ships with the ``mslm-memory`` package so that every MSLM install
automatically carries a native Hermes MemoryProvider.  No extra ``pip
install`` needed.

Usage (inside Hermes Agent)::

    from superlocalmemory.integrations.hermes import SuperLocalMemoryProvider

Tools exposed (v1):
  - ``slm_recall``    — 7-channel semantic search
  - ``slm_remember``  — explicit fact storage
  - ``slm_status``    — memory statistics

Lifecycle hooks:
  on_turn_start, on_session_end, on_session_switch,
  on_pre_compress, on_memory_write, shutdown
"""

from __future__ import annotations

import importlib
import json
import logging
import threading
from typing import Any, Dict, List, Optional

from agent.memory_manager import sanitize_context
from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROVIDER_NAME = "superlocalmemory"
_INIT_TIMEOUT = 30.0  # seconds for engine.initialize()
_PREFETCH_TIMEOUT = 8.0  # seconds for sync recall fallback
_MAX_CONTENT_LENGTH = 4000
_MAX_RECALL_LIMIT = 20
_PREFETCH_RECALL_LIMIT = 8
_PRE_COMPRESS_MSG_COUNT = 10
_PRE_COMPRESS_MSG_TRUNCATE = 500
_SEMANTIC_NOISE = frozenset({"", "ok", "yes", "thanks", "thx"})


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------

class SuperLocalMemoryProvider(MemoryProvider):
    """MSLM memory provider for Hermes Agent.

    Each instance manages a single ``MemoryEngine`` tied to one MSLM
    profile.  Prefetch uses a hybrid mode (first-turn synchronous,
    subsequent turns consume a background cache).  All writes are
    serialised through ``_write_lock``.
    """

    def __init__(self) -> None:
        # -- engine lifecycle ------------------------------------------------
        self._engine: Any = None
        self._slm_config: Any = None
        self._session_id: str = ""
        self._mslm_profile: str = ""

        # -- config-derived flags --------------------------------------------
        self._include_global: bool = True
        self._include_shared: bool = False
        self._cron_skipped: bool = False
        self._init_cancelled: bool = False

        # -- prefetch cache --------------------------------------------------
        self._prefetch_cache: str = ""
        self._prefetch_fired_at: int = -999

        # -- background threads ----------------------------------------------
        self._sync_thread: Optional[threading.Thread] = None
        self._prefetch_thread: Optional[threading.Thread] = None

        # -- locks -----------------------------------------------------------
        self._write_lock = threading.Lock()
        self._sync_turn_lock = threading.Lock()
        self._prefetch_lock = threading.Lock()

        # -- turn tracking ---------------------------------------------------
        self._turn_count: int = 0

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _parse_bool(value: Any, default: bool = False) -> bool:
        """Parse a boolean value from YAML config, handling string forms.

        Handles ``None``, ``bool``, ``str`` ("true"/"false"/"1"/"0"/"yes"/"no"
        /"on"/"off", case-insensitive), and ``int`` (1/0).

        Parameters
        ----------
        value:
            The raw config value (may be None, bool, str, or int).
        default:
            Returned when *value* is ``None``.

        Returns
        -------
        bool
            The parsed boolean.
        """
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        if isinstance(value, int):
            return value != 0
        return bool(value)

    def _load_hermes_config(self, hermes_home: str) -> Dict[str, str]:
        """Read ``memory.superlocalmemory`` section from Hermes config.yaml.

        Parameters
        ----------
        hermes_home:
            Path to the active HERMES_HOME directory (used by
            ``hermes_cli.config.load_config`` to locate the right config).

        Returns
        -------
        dict
            The overrides dict, or an empty dict on any error.
        """
        try:
            from hermes_cli.config import load_config

            config = load_config()
            if not config:
                return {}
            mem_config = config.get("memory", {})
            if not isinstance(mem_config, dict):
                return {}
            section = mem_config.get("superlocalmemory", {})
            return section if isinstance(section, dict) else {}
        except Exception:
            logger.debug("Failed to load Hermes config", exc_info=True)
            return {}

    # -- Provider metadata ---------------------------------------------------

    @property
    def name(self) -> str:
        return _PROVIDER_NAME

    # -- Availability check --------------------------------------------------

    def is_available(self) -> bool:
        """Return ``True`` if ``superlocalmemory`` is importable."""
        try:
            importlib.import_module("superlocalmemory")
            return True
        except ImportError:
            return False

    # -- Config schema -------------------------------------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "mslm_profile",
                "description": (
                    "MSLM profile name.  Leave empty to auto-detect from "
                    "Hermes profile."
                ),
                "required": False,
                "default": "",
            },
            {
                "key": "mode",
                "description": (
                    "MSLM operating mode: A (fully local, zero external API), "
                    "B (local Ollama for fact extraction), "
                    "C (cloud LLM for best quality)."
                ),
                "choices": ["A", "B", "C"],
                "default": "A",
            },
            {
                "key": "include_global",
                "description": (
                    "Include global-scope facts in search results "
                    "(cross-profile shared knowledge)."
                ),
                "type": "boolean",
                "default": True,
            },
            {
                "key": "include_shared",
                "description": (
                    "Include shared-scope facts in search results "
                    "(agent-to-agent memory)."
                ),
                "type": "boolean",
                "default": False,
            },
        ]

    # -- Engine health check -------------------------------------------------

    def _ensure_engine(self) -> bool:
        """Return ``True`` if the engine is available and initialised."""
        return self._engine is not None

    # -- Lifecycle: initialize -----------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialise the MSLM engine for a Hermes session.

        Configuration priority:
          1. Hermes config.yaml overrides (``memory.superlocalmemory.*``)
          2. MSLM native config (``~/.superlocalmemory/config.json``)
          3. Code defaults

        The engine initialisation runs in a daemon thread with a 30-second
        timeout to prevent model-loading stalls from blocking the agent.
        On timeout ``_init_cancelled`` is set and the engine is released.
        """
        # 1. Resolve MSLM profile name
        hermes_home = kwargs.get("hermes_home", "~/.hermes")
        agent_identity = kwargs.get("agent_identity", "default")
        config_override = self._load_hermes_config(hermes_home)
        self._mslm_profile = (
            config_override.get("mslm_profile")
            or agent_identity
            or "default"
        )

        # 2. Load MSLM config and apply overrides
        try:
            from superlocalmemory.core.config import SLMConfig

            self._slm_config = SLMConfig.load()
        except Exception:
            logger.warning("MSLM config load failed — provider disabled")
            return
        self._slm_config.active_profile = self._mslm_profile

        mode_override = config_override.get("mode")
        if mode_override:
            try:
                from superlocalmemory.storage.models import Mode

                self._slm_config.mode = Mode[mode_override]
            except KeyError:
                logger.warning(
                    "MSLM unknown mode '%s' — using config default",
                    mode_override,
                )

        # 3. Read recall-scope flags (type-safe bool parsing)
        self._include_global = self._parse_bool(
            config_override.get("include_global"), True,
        )
        self._include_shared = self._parse_bool(
            config_override.get("include_shared"), False,
        )

        # 4. Cron / flush guard — skip model loading for non-interactive contexts
        agent_context = kwargs.get("agent_context", "primary")
        platform = kwargs.get("platform", "cli")
        if agent_context in {"cron", "flush"} or platform == "cron":
            self._cron_skipped = True
            logger.debug("MSLM skipped: cron/flush context")
            return

        # 5. Create engine and initialise with timeout protection
        try:
            from superlocalmemory.core.engine import MemoryEngine

            self._engine = MemoryEngine(self._slm_config)

            init_error: Optional[Exception] = None

            self._init_cancelled = False

            def _do_init() -> None:
                nonlocal init_error
                try:
                    if self._init_cancelled:
                        return
                    self._engine.initialize()
                except Exception as exc:
                    init_error = exc

            init_thread = threading.Thread(
                target=_do_init, daemon=True, name="mslm-init",
            )
            init_thread.start()
            init_thread.join(timeout=_INIT_TIMEOUT)

            if init_thread.is_alive():
                logger.warning(
                    "MSLM engine init timed out after %ss — provider disabled",
                    _INIT_TIMEOUT,
                )
                self._init_cancelled = True
                init_thread.join(timeout=5.0)
                if init_thread.is_alive():
                    logger.warning(
                        "MSLM init thread did not terminate gracefully "
                        "— may retain model RAM",
                    )
                self._engine = None
                return

            if init_error:
                raise init_error

        except Exception as exc:
            logger.warning("MSLM engine init failed: %s — provider disabled", exc)
            self._engine = None
            return

        # 6. Pre-create speaker entities (non-fatal on failure)
        try:
            self._engine.create_speaker_entities("user", "hermes")
        except Exception as exc:
            logger.debug("MSLM create_speaker_entities failed (non-fatal): %s", exc)

        self._session_id = session_id
        logger.info(
            "MSLM provider ready — profile=%s mode=%s",
            self._mslm_profile,
            self._slm_config.mode.name,
        )

    # -- Lifecycle: shutdown -------------------------------------------------

    def shutdown(self) -> None:
        """Clean shutdown — wait for background threads, then release engine.

        Called when the agent shuts down or the provider is replaced.
        """
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=5.0)
        self._engine = None

    # -- Lifecycle: on_turn_start --------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Track turn number for cache-cadence control."""
        self._turn_count = turn_number

    # -- Prefetch: hybrid mode -----------------------------------------------

    def _format_recall_results(
        self, response: Any,
    ) -> str:
        """Format a ``RecallResponse`` into a concise prompt-injection string.

        Returns an empty string when there are no results.
        """
        if not response or not response.results:
            return ""

        lines = [f"[Memory recall: {response.query}]"]
        for r in response.results[:10]:
            content = (r.fact.content or "")[:200]
            lines.append(
                f"  • {content} "
                f"(score={r.score:.3f}, conf={r.confidence:.3f})",
            )
        return "\n".join(lines)

    def _sync_recall(self, query: str, **kwargs) -> str:
        """Synchronous engine recall with exception handling.

        Returns formatted string or empty string on failure.
        """
        try:
            limit = kwargs.get("limit", _PREFETCH_RECALL_LIMIT)
            fast = kwargs.get("fast", True)
            response = self._engine.recall(
                query, limit=limit, fast=fast,
                include_global=self._include_global,
                include_shared=self._include_shared,
            )
            return self._format_recall_results(response)
        except Exception as exc:
            logger.debug("MSLM recall failed: %s", exc)
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Hybrid prefetch: consume cache or fall back to synchronous recall.

        - Turn 1 (cold start): no cache → synchronous ``engine.recall()``.
        - Subsequent turns: consume ``_prefetch_cache`` written by the prior
          turn's ``queue_prefetch()``.
        """
        if self._cron_skipped or not self._engine:
            return ""
        if not query.strip():
            return ""

        # Consume cached result if available
        with self._prefetch_lock:
            if self._prefetch_cache:
                cached = self._prefetch_cache
                self._prefetch_cache = ""
                return cached

        # Fall back to synchronous recall
        return self._sync_recall(query)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue a background recall for the *next* turn's ``prefetch()``.

        Results are written to ``_prefetch_cache`` under ``_prefetch_lock``.
        """
        if self._cron_skipped or not self._engine or not query.strip():
            return

        def _do_prefetch() -> None:
            try:
                response = self._engine.recall(
                    query, limit=_PREFETCH_RECALL_LIMIT, fast=True,
                    include_global=self._include_global,
                    include_shared=self._include_shared,
                )
                formatted = self._format_recall_results(response)
                with self._prefetch_lock:
                    self._prefetch_cache = formatted
                    self._prefetch_fired_at = self._turn_count
            except Exception as exc:
                logger.debug("MSLM queue_prefetch failed: %s", exc)

        self._prefetch_thread = threading.Thread(
            target=_do_prefetch, daemon=True, name="mslm-prefetch",
        )
        self._prefetch_thread.start()

    # -- Lifecycle: sync_turn -------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Any = None,
    ) -> None:
        """Persist a completed turn as merged content.

        Content is combined in ``User: ...\\nHermes: ...`` format, truncated to
        ``_MAX_CONTENT_LENGTH`` (4000) characters.  Very short / noise-only
        turns are skipped.  The actual ``engine.store()`` runs on a background
        daemon thread protected by ``_write_lock``.

        If the previous turn's store is still in progress, this turn is dropped
        (no queue build-up).  The ``_sync_turn_lock`` prevents a race between
        ``is_alive()`` and ``thread.start()``.
        """
        if self._cron_skipped or not self._engine:
            return

        clean_user = (sanitize_context(user_content) or "").strip()
        clean_asst = (sanitize_context(assistant_content) or "").strip()

        # Semantic noise filter — skip very short / templatic responses
        if not clean_user or clean_user.strip().lower() in _SEMANTIC_NOISE:
            return

        combined = f"User: {clean_user}\nHermes: {clean_asst}"
        if len(combined) > _MAX_CONTENT_LENGTH:
            combined = combined[:_MAX_CONTENT_LENGTH]

        session = session_id or self._session_id

        def _sync() -> None:
            try:
                with self._write_lock:
                    self._engine.store(
                        combined,
                        session_id=session,
                        speaker="user",
                        scope="personal",
                    )
            except Exception as exc:
                logger.debug("MSLM sync_turn failed: %s", exc)

        # Drop if prior write is still in progress
        with self._sync_turn_lock:
            if self._sync_thread and self._sync_thread.is_alive():
                logger.debug("MSLM sync_turn: prior write in progress, dropping")
                return
            self._sync_thread = threading.Thread(
                target=_sync, daemon=True, name="mslm-sync",
            )
            self._sync_thread.start()

    # -- Lifecycle: on_memory_write ------------------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Any = None,
    ) -> None:
        """Mirror built-in memory writes to MSLM (personal scope)."""
        if not self._ensure_engine() or self._cron_skipped or not content:
            return

        def _write() -> None:
            try:
                with self._write_lock:
                    self._engine.store(
                        content, session_id=self._session_id,
                        scope="personal",
                    )
            except Exception as exc:
                logger.debug("MSLM on_memory_write failed: %s", exc)

        t = threading.Thread(target=_write, daemon=True, name="mslm-memwrite")
        t.start()

    # -- Lifecycle: on_pre_compress ------------------------------------------

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Store a summary of the last N messages before compression.

        Returns an empty string (does not interfere with the compression
        summary prompt).
        """
        if self._cron_skipped or not self._engine:
            return ""

        parts: List[str] = []
        for msg in messages[-_PRE_COMPRESS_MSG_COUNT:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip() and role in {"user", "assistant"}:
                parts.append(f"{role}: {content[:_PRE_COMPRESS_MSG_TRUNCATE]}")

        if not parts:
            return ""

        combined = "[Pre-compression context]\n" + "\n".join(parts)

        def _flush() -> None:
            try:
                with self._write_lock:
                    self._engine.store(
                        combined, session_id=self._session_id,
                        speaker="system", scope="personal",
                    )
            except Exception as exc:
                logger.debug("MSLM pre-compress store failed: %s", exc)

        t = threading.Thread(target=_flush, daemon=True, name="mslm-compress")
        t.start()
        return ""

    # -- Lifecycle: on_session_end -------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Close the MSLM session on session end."""
        if self._ensure_engine() and not self._cron_skipped:
            try:
                self._engine.close_session(self._session_id)
            except Exception as exc:
                logger.debug("MSLM close_session failed: %s", exc)

    # -- Lifecycle: on_session_switch ----------------------------------------

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Update session ID and clear the prefetch cache."""
        self._session_id = new_session_id
        with self._prefetch_lock:
            self._prefetch_cache = ""

    # -- System prompt block -------------------------------------------------

    def system_prompt_block(self) -> str:
        """Return a status block describing MSLM connection and available tools.

        Injects dynamic profile name, mode, and fact count into the system
        prompt so the model is aware of the memory backend.
        """
        if not self._ensure_engine():
            return ""
        try:
            rows = self._engine.db.execute(
                "SELECT COUNT(*) FROM atomic_facts "
                "WHERE profile_id = ?",
                (self._mslm_profile,),
            )
            fact_count = rows[0][0] if rows else 0
        except Exception:
            fact_count = 0

        return (
            f"[SuperLocalMemory Status]\n"
            f"Profile: {self._mslm_profile} | "
            f"Mode: {getattr(self._slm_config.mode, 'name', '?')} | "
            f"Facts: {fact_count}\n\n"
            f"Available tools:\n"
            f"- slm_recall(query, limit=10, fast=false): 语义搜索本地记忆库。"
            f"7通道检索 + RRF融合排序。\n"
            f"- slm_remember(content, scope=\"personal\"): 显式存储信息到本地记忆库。"
            f"scope 可选 \"personal\"(仅当前profile) 或 \"global\"(跨profile共享)。\n"
            f"- slm_status(): 查看记忆库统计信息"
            f"（事实数、实体数、数据库大小等）。\n"
        )

    # -- Tool schemas --------------------------------------------------------

    _RECALL_SCHEMA = {
        "name": "slm_recall",
        "description": "语义搜索本地记忆库。7 通道检索 + RRF 融合排序。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然语言搜索查询",
                },
                "limit": {
                    "type": "integer",
                    "description": "最大返回数",
                    "default": 10,
                },
                "fast": {
                    "type": "boolean",
                    "description": "跳过扩散激活通道以加速",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    }

    _REMEMBER_SCHEMA = {
        "name": "slm_remember",
        "description": "显式存储信息到本地记忆库。自动实体提取 + 图谱构建 + 向量嵌入。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的信息，写成清晰的事实陈述",
                },
                "scope": {
                    "type": "string",
                    "description": "作用域: personal (默认) | global",
                    "default": "personal",
                    "enum": ["personal", "global"],
                },
            },
            "required": ["content"],
        },
    }

    _STATUS_SCHEMA = {
        "name": "slm_status",
        "description": "查看本地记忆库状态（事实数、实体数、数据库大小等）。",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    }

    _V1_TOOL_SCHEMAS = [_RECALL_SCHEMA, _REMEMBER_SCHEMA, _STATUS_SCHEMA]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas for ``slm_recall``, ``slm_remember``, ``slm_status``."""
        return list(self._V1_TOOL_SCHEMAS)

    # -- Tool call dispatch --------------------------------------------------

    def handle_tool_call(
        self, tool_name: str, args: Dict[str, Any], **kwargs,
    ) -> str:
        """Dispatch a tool call to the appropriate handler.

        Returns a JSON string (result or error).  All exceptions are caught
        and returned as ``tool_error`` so the calling agent never crashes.
        """
        try:
            if tool_name == "slm_recall":
                return self._tool_recall(args)
            if tool_name == "slm_remember":
                return self._tool_remember(args)
            if tool_name == "slm_status":
                return self._tool_status(args)
            return tool_error(f"Unknown tool: {tool_name}")
        except Exception as exc:
            logger.debug("MSLM tool call '%s' failed: %s", tool_name, exc)
            return tool_error(f"Tool {tool_name} failed: {exc}")

    # -- Tool implementations ------------------------------------------------

    def _tool_recall(self, params: Dict[str, Any]) -> str:
        """Handle ``slm_recall`` tool call."""
        if not self._ensure_engine():
            return tool_error("SuperLocalMemory engine not ready")

        query = params.get("query", "").strip()
        if not query:
            return tool_error("query is required")

        limit = min(int(params.get("limit", 10)), _MAX_RECALL_LIMIT)
        fast = bool(params.get("fast", False))

        try:
            response = self._engine.recall(
                query, limit=limit, fast=fast,
                include_global=self._include_global,
                include_shared=self._include_shared,
            )
        except Exception as exc:
            logger.debug("MSLM recall failed: %s", exc)
            return tool_error(f"Recall failed: {exc}")

        results_json = []
        for r in response.results:
            results_json.append({
                "fact_id": r.fact.fact_id,
                "content": r.fact.content,
                "score": round(r.score, 4),
                "confidence": round(r.confidence, 4),
                "channel_scores": r.channel_scores,
            })

        result_obj = {
            "results": results_json,
            "count": len(results_json),
            "query_type": response.query_type,
            "retrieval_time_ms": response.retrieval_time_ms,
        }
        return json.dumps(result_obj, ensure_ascii=False)

    def _tool_remember(self, params: Dict[str, Any]) -> str:
        """Handle ``slm_remember`` tool call."""
        if not self._ensure_engine():
            return tool_error("SuperLocalMemory engine not ready")

        content = (params.get("content") or "").strip()
        if not content:
            return tool_error("content is required")

        scope = params.get("scope", "personal")
        if scope not in ("personal", "global"):
            scope = "personal"

        try:
            with self._write_lock:
                fact_ids = self._engine.store(
                    content, session_id=self._session_id, scope=scope,
                )
        except Exception as exc:
            logger.debug("MSLM store failed: %s", exc)
            return tool_error(f"Store failed: {exc}")

        if not fact_ids:
            return json.dumps(
                {"status": "noop", "message": "No new facts extracted (content may be redundant)."},
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "status": "stored",
                "fact_ids": fact_ids,
                "message": f"Stored {len(fact_ids)} facts from your content.",
            },
            ensure_ascii=False,
        )

    def _tool_status(self, params: Dict[str, Any]) -> str:
        """Handle ``slm_status`` tool call."""
        if not self._ensure_engine():
            return tool_error("SuperLocalMemory engine not ready")

        profile = self._mslm_profile
        mode = getattr(self._slm_config.mode, "name", "?")
        db = self._engine.db

        facts_total = 0
        entities = 0
        graph_edges = 0
        db_size_mb = 0.0
        embedding_model = ""
        embedding_dim = 0

        try:
            rows = db.execute(
                "SELECT COUNT(*) FROM atomic_facts WHERE profile_id = ?",
                (self._mslm_profile,),
            )
            facts_total = rows[0][0] if rows else 0
        except Exception:
            pass

        try:
            rows = db.execute("SELECT COUNT(*) FROM kg_nodes")
            entities = rows[0][0] if rows else 0
        except Exception:
            pass

        try:
            rows = db.execute("SELECT COUNT(*) FROM memory_edges")
            graph_edges = rows[0][0] if rows else 0
        except Exception:
            pass

        try:
            import os
            db_path = getattr(db, "_db_path", None) or str(
                self._slm_config.base_dir / "memory.db",
            )
            if os.path.exists(db_path):
                db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 1)
        except Exception:
            pass

        try:
            emb_cfg = self._slm_config.embedding
            if emb_cfg:
                _m = emb_cfg.model_name
                if _m is not None:
                    embedding_model = str(_m)
                _d = emb_cfg.dim
                if _d is not None:
                    embedding_dim = int(_d)
        except Exception:
            pass

        result_obj = {
            "profile": profile,
            "mode": mode,
            "facts": {"total": int(facts_total)},
            "entities": int(entities),
            "graph_edges": int(graph_edges),
            "db_size_mb": float(db_size_mb),
            "embedding_model": str(embedding_model),
            "embedding_dim": int(embedding_dim),
        }
        try:
            return json.dumps(result_obj, ensure_ascii=False)
        except TypeError:
            # Last-resort sanitisation: stringify everything
            sanitised = {k: str(v) for k, v in result_obj.items()}
            return json.dumps(sanitised, ensure_ascii=False)

    # -- Plugin registration -------------------------------------------------


def register(ctx: Any) -> None:
    """Hermes plugin entry point — registers the provider.

    Works with both the real ``PluginContext`` (``register_provider``)
    and the discovery-time ``_ProviderCollector``
    (``register_memory_provider``).
    """
    provider = SuperLocalMemoryProvider()

    # Hermes discovery path: _ProviderCollector.register_memory_provider()
    if hasattr(ctx, "register_memory_provider"):
        ctx.register_memory_provider(provider)
        return

    # Hermes runtime path: real PluginContext.register_provider()
    if hasattr(ctx, "register_provider"):
        ctx.register_provider(provider)
        return

    logger.warning("register() called with unknown context type: %s", type(ctx))
