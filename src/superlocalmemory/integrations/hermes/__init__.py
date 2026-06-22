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
# Helper functions
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


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

    # -- Lifecycle: initialize -----------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize the provider for a Hermes session.

        (Stub for Chunk 1 — full implementation in Chunk 2.)
        """
        self._session_id = session_id
        self._mslm_profile = kwargs.get("agent_identity", "default")

    # -- Lifecycle: shutdown -------------------------------------------------

    def shutdown(self) -> None:
        """Clean up resources.

        (Stub for Chunk 1 — full implementation in Chunk 2.)
        """
        self._engine = None

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
