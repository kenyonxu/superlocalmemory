# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Frozen document transform for already-written retrieval-v1 vectors."""
import math
import re
from collections import Counter

_TOKEN = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_-]{23,}\b")


def document_text(text):
    def clean(match):
        token = match.group()
        probabilities = [n / len(token) for n in Counter(token.lower()).values()]
        entropy = -sum(p * math.log2(p) for p in probabilities)
        is_hex = bool(re.fullmatch(r"[0-9a-fA-F-]+", token))
        mixed = any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
        return "" if is_hex or (mixed and entropy >= 3.0) else token
    return " ".join(_TOKEN.sub(clean, text).split())
