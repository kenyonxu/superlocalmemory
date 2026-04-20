# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory v3.4.21 — Stage 8 F5 (Mediums/Lows)

"""Stage 8 F5 regression — marker regex + LIMIT hardening.

S-L04: ``post_tool_outcome_hook._MARKER_RE`` now disallows colons in
the fact_id group, so a hostile tool response cannot emit
``slm:fact:evil:deadbeef:abcdef01`` and confuse the validator with a
wrong-grouped fact_id.

SEC-M2: the pending-window SELECT uses ``LIMIT 5`` (down from 20) to
reduce amplification surface on tool-response spam.
"""

from __future__ import annotations

from superlocalmemory.hooks import post_tool_outcome_hook as h


def test_s_l04_marker_regex_rejects_colon_in_fact_id() -> None:
    m = h._MARKER_RE.search(  # noqa: SLF001
        "before slm:fact:evil:deadbeef:abcdef01 after"
    )
    assert m is not None, "regex should find *some* match"
    # The fact_id group MUST NOT contain a colon — that was the bypass.
    assert ":" not in m.group(1), m.group(1)


def test_s_l04_marker_regex_accepts_hex_and_legacy_fact_ids() -> None:
    # Hex fact_id (production shape: uuid4().hex[:16]).
    m1 = h._MARKER_RE.search("slm:fact:abcdef0123456789:deadbeef")  # noqa: SLF001
    assert m1 is not None and m1.group(1) == "abcdef0123456789"
    # Legacy dash-style fact_id (matches existing test_outcome_hooks fixtures).
    m2 = h._MARKER_RE.search("x slm:fact:fact-42:deadbeef y")  # noqa: SLF001
    assert m2 is not None and m2.group(1) == "fact-42"


def test_sec_m2_pending_limit_is_tightened() -> None:
    # Check the actual SQL literal rather than scanning prose comments —
    # the comment block explaining SEC-M2 mentions the old value by
    # design for auditability.
    src = open(h.__file__, encoding="utf-8").read()
    # The executed query string ends with "ORDER BY created_at_ms DESC LIMIT 5".
    assert "DESC LIMIT 5\"" in src or 'DESC LIMIT 5"' in src, (
        "SEC-M2: pending window must be LIMIT 5 in the executed SQL"
    )
    # And the tightened literal is NOT the old LIMIT 20 SQL form.
    assert "DESC LIMIT 20\"" not in src, (
        "SEC-M2: pending window LIMIT 20 SQL regressed"
    )
