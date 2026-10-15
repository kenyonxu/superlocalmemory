# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""What is injected into an agent's context never exceeds the token budget."""

from __future__ import annotations

from superlocalmemory.core import injection as inj


def _mem(i, chars=2000, importance=0.0):
    return inj.InjectableMemory(content=f"memory {i} " + "x" * chars, score=0.9,
                                fact_id=f"f{i}", importance=importance, access_count=0)


def test_the_rendered_block_including_its_framing_fits_the_budget() -> None:
    mems = [_mem(i, importance=0.95) for i in range(5)] + [_mem(i + 10) for i in range(20)]
    for mode in ("A", "B", "C"):
        text = inj.render_context(mems, mode=mode, cfg=None, wrap=True)
        assert inj.estimate_tokens(text) <= inj.resolve_budget(mode, None), mode
        assert text.rstrip().endswith(inj.UNTRUSTED_CONTEXT_END)  # the wrapper stays whole


def test_cap_tokens_trims_on_a_line_and_says_so() -> None:
    text = "\n".join(f"- line {i} " + "y" * 40 for i in range(100))
    capped = inj.cap_tokens(text, 50)
    assert inj.estimate_tokens(capped) <= 50 and capped.endswith(inj.TRIMMED_MARK)
    assert inj.cap_tokens("short", 50) == "short"
