# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Normalize a model's first person using the supporting source turn, never a guess."""

from __future__ import annotations

from dataclasses import replace

from superlocalmemory.encoding.assertion_support import words
from superlocalmemory.encoding.speaker_spans import normalize_speaker, speaker_spans


def grounded_speakers(facts, source: str):
    spans = speaker_spans(source)
    result = []
    for fact in facts:
        scores = [(len(words(fact.content) & words(span.text)), span) for span in spans]
        if not scores:
            continue
        best = max(score for score, _ in scores)
        actors = {span.speaker for score, span in scores if score == best}
        if len(actors) == 1:
            result.append(replace(fact, content=normalize_speaker(fact.content, actors.pop())))
        else:
            # A third-person fact can stand alone; ambiguous first person cannot.
            if normalize_speaker(fact.content, "the user") == fact.content:
                result.append(fact)
    return result
