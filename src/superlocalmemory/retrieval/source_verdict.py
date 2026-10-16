# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Apply source proofs without rewriting the local model's probabilities."""

from __future__ import annotations

from superlocalmemory.retrieval import answer_question_forms as forms
from superlocalmemory.retrieval.explicit_answer import RULE_ID as APPROVER_ID
from superlocalmemory.retrieval.explicit_answer import support_indices
from superlocalmemory.retrieval.explicit_slots import RULE_ID as SLOT_ID
from superlocalmemory.retrieval.explicit_slots import rejects_slot, supports_slot


def base_identity(identity):
    suffixes = (forms.RULE_SUFFIX, "+" + APPROVER_ID, "+" + SLOT_ID)
    while True:
        previous = identity
        for suffix in suffixes:
            identity = identity.removesuffix(suffix)
        if previous == identity:
            return identity


def settle(question, documents, verdict):
    from superlocalmemory.retrieval.sufficiency import SufficiencyVerdict

    permission = forms.rule_support(question, documents)
    approver = support_indices(question, documents)
    slots = tuple(i for i, doc in enumerate(documents) if supports_slot(question, doc))
    support = tuple(
        sorted(set(verdict.rule_support) | set(permission) | set(approver) | set(slots))
    )
    rejected = not support and rejects_slot(question, documents)
    identity = base_identity(verdict.calibration_id)
    if permission:
        identity += forms.RULE_SUFFIX
    if approver:
        identity += "+" + APPROVER_ID
    if slots or rejected:
        identity += "+" + SLOT_ID
    return SufficiencyVerdict(
        verdict.probabilities,
        verdict.threshold,
        identity,
        verdict.calibration_status,
        verdict.backend,
        support,
        rejected,
        len(documents),
    )
