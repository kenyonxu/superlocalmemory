# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Never move text onto a managed model the answer check was not fitted for.

Recall decides "this is in your memory" with the answer-support recipe
(retrieval/support_recipe.py), which is fitted per text model. "Upgrade memory
engine" moved the author's text to EmbeddingGemma 2 before any recipe existed
for it, and recall refused correct answers on the whole store (13 Oct 2026).

Only the managed models are guarded: they are the ones SuperLocalMemory offers
and upgrades to by itself. A model a person configures (OpenAI, Ollama) is
their choice and keeps today's recipe, as before.
"""

from __future__ import annotations

from typing import Any

MANAGED_PROVIDER = "slm-media"


def refusal_for(target: Any) -> str | None:
    """Why text may not move to ``target`` yet, or None when it may."""
    from superlocalmemory.retrieval import support_recipe

    if getattr(target, "provider", "") != MANAGED_PROVIDER:
        return None
    model = str(getattr(target, "model_name", "") or "")
    if support_recipe.approved_for_text(model):
        return None
    return (f"Recall's answer check is not calibrated for {model} yet: on it, recall can say a "
            "memory is not there when it is. Pictures and documents use it already. To move "
            "your text anyway, use force (slm embedder switch --force).")


def live_advice(live: Any) -> str | None:
    """What to tell a store whose text ALREADY runs on a managed model that is not
    approved for text.

    4.1.25 to 4.2.1 offered "Upgrade memory engine" (the dashboard card and
    slm embedder upgrade), which moved text there. Rolling back is the person's call, never automatic: their previous
    vectors are kept, so it takes minutes.
    """
    if refusal_for(live) is None:
        return None
    model = str(getattr(live, "model_name", "") or "")
    return (f"Text search runs on {model}, and recall's answer check is not calibrated for it "
            "yet, so recall may say a memory is not there when it is. Go back to the previous "
            "engine: Roll back below, or slm embedder rollback. Your memories are kept either "
            "way, and it takes minutes.")


def answer_check_status(live: Any) -> dict:
    """The answer check for the live text model, as every surface reports it
    (MCP ``health``; the dashboard card and ``slm doctor`` show ``live_advice``)."""
    from superlocalmemory.retrieval import support_recipe

    model = str(getattr(live, "model_name", "") or "")
    advice = live_advice(live) or ""
    recipe = support_recipe.for_model(model) or support_recipe.ACTIVE
    return {"status": "warn" if advice else "ok", "model": model, "recipe": recipe.version,
            "approved_for_text": support_recipe.approved_for_text(model), "advice": advice}


__all__ = ["MANAGED_PROVIDER", "answer_check_status", "live_advice", "refusal_for"]
