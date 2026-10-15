# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Who agreed to send memories to Jev, when, and to which notice (4.1.25 audit OPS-19).

Consent to the online answer check was one boolean in the settings file. A
withdrawal overwrote the earlier yes, and nothing said who had agreed, when, or
to what wording, so the installation could not show what a person accepted
when it started sending their memories out.

Every change of consent (granted or withdrawn, for the answer check and for
Jev's reordering) now appends a receipt to the hash-chained audit trail before
the setting is saved: the acting person, the time (the entry's own), the
purpose, the provider, what is sent, and the notice's version and SHA-256. If
the receipt cannot be written the setting is not changed (the caller answers
503). Receipts hold no prompt, memory or key. They are exported with the audit
trail and kept as long as it is.

The notice texts below are the words the dashboard shows next to the checkbox
(``ui/js/answer-check.js``); a test keeps the two identical.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException, Request

#: (version, text) of each notice; bump the version whenever the text changes.
ANSWER_CHECK_NOTICE = (
    "jev-answer-check/2026-10",
    "When on, each recall sends your question and its top 3 memories to {provider}. "
    "Don’t use this for client, confidential or personal material.",
)
RERANK_NOTICE = (
    "jev-rerank/2026-10",
    "When on, each recall sends your question and its top {k} memories to {provider} "
    "to choose the best order. Don't use this for client, confidential or personal material.",
)
_PROVIDER_LABELS = {"typesafe": "TypeSafe", "openrouter": "OpenRouter"}
_KINDS = {
    "answer_check": (ANSWER_CHECK_NOTICE, "sufficiency_jev_consent",
                     "judge whether the recalled memories answer the question",
                     "the question and its top 3 memories"),
    "rerank": (RERANK_NOTICE, "sufficiency_jev_rerank_consent",
               "reorder the recalled memories", "the question and its top memories"),
}


def rerank_count(retrieval: Any) -> int:
    """How many memories the reordering notice names (the clamped setting)."""
    try:
        from superlocalmemory.retrieval.jev_rerank import clamp_rerank_k

        return int(clamp_rerank_k(getattr(retrieval, "sufficiency_jev_rerank_k", None)))
    except Exception:  # noqa: BLE001 - wording only
        return 0


def notice_text(kind: str, provider: str, k: int = 0) -> str:
    template = _KINDS[kind][0][1]
    return template.replace("{provider}", _PROVIDER_LABELS.get(provider, provider)) \
        .replace("{k}", str(k))


def _actor(request: Request) -> str:
    from superlocalmemory.server.rbac_enforce import resolve_principal

    principal = resolve_principal(request)
    if principal.get("kind") == "user":
        return f"user:{principal['user_id']}"
    return str(getattr(request.state, "authenticated_actor", "") or "owner")


def record_change(request: Request, kind: str, *, before: Any, accepted: bool,
                  provider: str, k: int = 0) -> bool:
    """Append a receipt when ``kind``'s consent changes. Raises 503 when it cannot.

    Returns whether a receipt was written (False: nothing changed).
    """
    (version, _text), field, purpose, payload = _KINDS[kind]
    was = getattr(before, field, False) is True
    same_provider = str(getattr(before, "sufficiency_jev_provider", "") or "") == provider
    if was == accepted and (not accepted or same_provider):
        return False
    text = notice_text(kind, provider, k)
    from superlocalmemory.compliance.audit import AuditChain
    from superlocalmemory.infra.data_root import state_path

    try:
        AuditChain(str(state_path("audit_chain.db"))).log(
            operation=f"consent.jev.{kind}", agent_id=_actor(request),
            metadata={
                "decision": "granted" if accepted else "withdrawn",
                "purpose": purpose, "provider": provider, "sends": payload,
                "notice_version": version,
                "notice_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
        )
    except Exception as exc:  # noqa: BLE001 - no receipt, no change
        raise HTTPException(
            503, detail="The consent could not be recorded, so nothing was changed. Try again.",
        ) from exc
    return True


__all__ = ["ANSWER_CHECK_NOTICE", "RERANK_NOTICE", "notice_text", "record_change",
           "rerank_count"]
