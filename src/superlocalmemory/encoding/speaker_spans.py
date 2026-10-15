# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Speaker and quotation boundaries for conservative source normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TURN = re.compile(
    r"(?:^|(?<=[.!?])\s+|\n)\[([\w -]+)\]:?\s*"
    r"|(?:^|\n)(User|Assistant|System):\s*",
    re.I,
)
# Apostrophes within words are contractions, not quote boundaries.
_QUOTE = re.compile(
    r'"[^"]*"|“[^”]*”|(?<!\w)\'(?:[^\']|(?<=\w)\'(?=\w))*\'(?!\w)'
    r"|‘(?:[^’]|(?<=\w)’(?=\w))*’"
)


@dataclass(frozen=True)
class SpeakerSpan:
    text: str
    speaker: str
    start: int
    end: int


def speaker_spans(text: str, default_speaker: str = "the user") -> list[SpeakerSpan]:
    quoted = [(m.start(), m.end()) for m in _QUOTE.finditer(text)]
    matches = [
        m
        for m in _TURN.finditer(text)
        if not any(start <= m.start() < end for start, end in quoted)
    ]
    if not matches:
        return [SpeakerSpan(text, default_speaker or "the user", 0, len(text))]
    spans = []
    if text[: matches[0].start()].strip():
        spans.append(
            SpeakerSpan(text[: matches[0].start()], default_speaker, 0, matches[0].start())
        )
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        name = match.group(1) or match.group(2)
        name = "the user" if name.lower() == "user" else name
        spans.append(SpeakerSpan(text[match.end() : end], name, match.end(), end))
    return spans


def outside_quotes(text: str, transform) -> str:
    result, start = [], 0
    for match in _QUOTE.finditer(text):
        result.extend((transform(text[start : match.start()]), match.group(0)))
        start = match.end()
    result.append(transform(text[start:]))
    return "".join(result)


def normalize_speaker(text: str, speaker: str) -> str:
    """Resolve only the outer speaker's first person, preserving every quotation."""
    if not speaker or re.search(r"(?m)^\s*(?:```|def \w+\(|class \w+|\w+\s*=)", text):
        return text.strip()

    def rewrite(part: str) -> str:
        substitutions = [
            (r"\bI['’]m\b|\bI am\b", f"{speaker} is"),
            (r"\bI['’]ve\b|\bI have\b", f"{speaker} has"),
            (r"\bI['’]ll\b", f"{speaker} will"),
            (r"\bI['’]d\b", f"{speaker} would"),
            (r"\bmy\b", f"{speaker}'s"),
            (r"\bI\b|\bme\b|\bmyself\b", speaker),
        ]
        for pattern, replacement in substitutions:
            part = re.sub(pattern, lambda _: replacement, part, flags=re.I)
        return part

    return outside_quotes(text, rewrite).strip()


def normalized_spans(source: str, default_speaker: str = "the user") -> list[str]:
    return [
        normalize_speaker(span.text, span.speaker)
        for span in speaker_spans(source, default_speaker)
    ]


def quoted_texts(text: str) -> list[str]:
    return [m.group(0)[1:-1] for m in _QUOTE.finditer(text)]


def bounded_turns(
    turns: list[str], limit: int = 6000, speaker_a: str = "", speaker_b: str = ""
) -> list[str]:
    """Bound each model request; retain every source character across the chunks."""
    pieces = []
    for index, turn in enumerate(turns):
        if speaker_a or speaker_b:
            actor = speaker_a if index % 2 == 0 else (speaker_b or speaker_a)
            turn = with_actor(turn, actor)
        for span in speaker_spans(turn):
            prefix = "" if span.speaker == "the user" else f"[{span.speaker}]: "
            budget = max(256, limit - len(prefix))
            pieces.extend(
                prefix + span.text[i : i + budget] for i in range(0, len(span.text), budget)
            )
    return pieces


def split_sentences(text: str) -> list[str]:
    """Only punctuation outside a quoted span can end an extraction sentence."""
    spans = [(match.start(), match.end()) for match in _QUOTE.finditer(text)]
    boundaries = [
        match.end()
        for match in re.finditer(r"(?<=[.!?])\s+", text)
        if not any(start <= match.start() < end for start, end in spans)
    ]
    starts = [0, *boundaries]
    ends = [*boundaries, len(text)]
    return [
        text[start:end].strip()
        for start, end in zip(starts, ends)
        if len(text[start:end].strip()) >= 8
    ]


def with_actor(source: str, speaker: str = "", role: str = "user") -> str:
    """Add authoritative write metadata to extraction context, never to raw storage."""
    if _TURN.match(source.lstrip()):
        return source
    actor = speaker or ("User" if role == "user" else role.title())
    return f"[{actor}]: {source}"
