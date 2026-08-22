# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""What the running mode can do with a model, in words a user can act on.

WHY THIS EXISTS

Mode A runs with no language model at all. That is the point of it: nothing on
the store or recall path calls out to anything, and a summary is assembled
directly from the user's own notes rather than written.

The surfaces already reported *what* they did -- a summary came back labelled
"assembled directly from your own notes" -- but never *why*, and never what to do
about it. Someone looking at a plainer summary than they expected had no way to
learn that a written one needs a model and which modes have one. They would
reasonably conclude the feature was broken.

So this returns the mode, whether a model is available, and one sentence naming
the next step. Every model-backed surface returns the same block, so the
explanation is identical wherever it appears rather than reworded per pane.

WHAT IT IS NOT

It is not a capability gate. Nothing here decides whether a call is made; the
mode's configuration does that. This only describes the decision to the person
looking at the result.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Named so the message can say which modes to consider without hard-coding the
#: sentence at each call site.
_MODE_WITH_LOCAL_MODEL = "B"
_MODE_WITH_CLOUD_MODEL = "C"

_NO_MODEL_MESSAGE = (
    "This mode runs entirely on your machine with no language model, so "
    "summaries are assembled from your own notes rather than written. For "
    f"written summaries and the other model-backed features, switch to Mode "
    f"{_MODE_WITH_LOCAL_MODEL} (a local model) or Mode {_MODE_WITH_CLOUD_MODEL} "
    "(your own cloud model) in Settings and connect one."
)

_MODEL_CONFIGURED_BUT_ABSENT = (
    "This mode uses a language model, but none is reachable right now, so "
    "results fall back to being assembled from your own notes. Check the model "
    "settings, and that the local model server is running if you are using one."
)


def llm_capability(config: Any, *, llm_reachable: bool | None = None) -> dict:
    """Describe this mode's model support for a user-facing surface.

    ``llm_reachable`` lets a caller that already knows the answer pass it in --
    a summary route has just tried and knows whether it worked, and asking again
    would mean a second connection attempt to say something it already knows.
    Left as None, availability is taken from configuration alone.
    """
    mode = ""
    provider = ""
    try:
        mode = str(getattr(getattr(config, "mode", None), "value", "") or "").upper()
        provider = str(getattr(getattr(config, "llm", None), "provider", "") or "")
    except Exception as exc:  # noqa: BLE001 -- a description must not raise
        logger.debug("mode capability: cannot read config: %s", exc)

    mode_has_model = mode in (_MODE_WITH_LOCAL_MODEL, _MODE_WITH_CLOUD_MODEL)
    configured = bool(provider) and mode_has_model
    available = configured if llm_reachable is None else bool(llm_reachable)

    if not mode_has_model:
        message = _NO_MODEL_MESSAGE
    elif not available:
        message = _MODEL_CONFIGURED_BUT_ABSENT
    else:
        message = ""

    return {
        "mode": mode or "?",
        "llm_available": bool(available),
        "llm_provider": provider,
        "message": message,
    }
