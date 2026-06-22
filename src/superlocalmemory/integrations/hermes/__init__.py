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

    # -- Tool schemas --------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas for ``slm_recall``, ``slm_remember``, ``slm_status``.

        (Stub for Chunk 1 — full implementation in Chunk 5.)
        """
        return []

    # -- Plugin registration -------------------------------------------------


def register(ctx) -> None:
    """Hermes plugin entry point — registers the provider."""
    from hermes_cli.plugin_context import PluginContext

    if not isinstance(ctx, PluginContext):
        raise TypeError("register() requires a PluginContext")
    ctx.register_provider(SuperLocalMemoryProvider)
