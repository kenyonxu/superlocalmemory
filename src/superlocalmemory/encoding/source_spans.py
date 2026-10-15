# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Exact source spans must end at token boundaries, not inside a numeric value."""

from __future__ import annotations

import re


def exact_span(fact: str, source: str) -> tuple[int, int] | None:
    offset = 0
    while fact:
        start = source.find(fact, offset)
        if start < 0:
            return None
        end = start + len(fact)
        before, after = source[:start], source[end:]
        left = not (before and fact[0].isalnum() and (before[-1].isalnum() or before[-1] == "_"))
        right = not (after and fact[-1].isalnum() and (after[0].isalnum() or after[0] == "_"))
        partial_number = bool(re.search(r"\d[.,]$", fact) and after[:1].isdigit())
        partial_number |= bool(fact[-1:].isdigit() and re.match(r"[.,]\d", after))
        if left and right and not partial_number:
            return start, end
        offset = start + 1
    return None
