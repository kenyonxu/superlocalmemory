# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Parsers for ingestion file formats: SRT, VTT, MBOX, ICS."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NamedTuple


class Utterance(NamedTuple):
    speaker: str
    text: str
    timestamp: str


def parse_srt(filepath: str | Path) -> list[Utterance]:
    """Parse SubRip (.srt) file into utterances."""
    content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\n+", content.strip())
    utterances = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        # Line 1: sequence number, Line 2: timestamps, Line 3+: text
        timestamp = lines[1].strip() if len(lines) > 1 else ""
        text = " ".join(lines[2:]).strip()
        if text:
            # Try to extract speaker from "Speaker: text" pattern
            speaker, content_text = _extract_speaker(text)
            utterances.append(Utterance(speaker=speaker, text=content_text, timestamp=timestamp))
    return utterances


def parse_vtt(filepath: str | Path) -> list[Utterance]:
    """Parse WebVTT (.vtt) file into utterances."""
    content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    # Remove WEBVTT header
    content = re.sub(r"^WEBVTT.*?\n\n", "", content, flags=re.DOTALL)
    blocks = re.split(r"\n\n+", content.strip())
    utterances = []
    for block in blocks:
        lines = block.strip().split("\n")
        timestamp = ""
        text_lines = []
        for line in lines:
            if "-->" in line:
                timestamp = line.strip()
            elif line.strip() and not line.strip().isdigit():
                # Remove VTT tags like <v Speaker>
                clean = re.sub(r"<v\s+([^>]+)>", r"\1: ", line)
                clean = re.sub(r"<[^>]+>", "", clean).strip()
                if clean:
                    text_lines.append(clean)
        text = " ".join(text_lines)
        if text:
            speaker, content_text = _extract_speaker(text)
            utterances.append(Utterance(speaker=speaker, text=content_text, timestamp=timestamp))
    return utterances


def parse_transcript_file(filepath: str | Path) -> tuple[str, list[str]]:
    """Parse any transcript file (.srt, .vtt, .txt).

    Returns (combined_text, list_of_speakers).
    """
    path = Path(filepath)
    suffix = path.suffix.lower()

    if suffix == ".srt":
        utterances = parse_srt(path)
    elif suffix == ".vtt":
        utterances = parse_vtt(path)
    else:
        # Plain text — treat entire file as one utterance
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:5000], []

    speakers = list({u.speaker for u in utterances if u.speaker != "unknown"})
    combined = "\n".join(f"[{u.speaker}] {u.text}" for u in utterances)
    return combined[:5000], speakers


def content_hash(filepath: str | Path) -> str:
    """SHA256 of file content (first 32 chars). Path-independent for dedup."""
    content = Path(filepath).read_bytes()
    return hashlib.sha256(content).hexdigest()[:32]


def _extract_speaker(text: str) -> tuple[str, str]:
    """Extract speaker from 'Speaker: text' or 'Speaker Name: text' pattern."""
    match = re.match(r"^([A-Z][a-zA-Z\s]{0,30}):\s*(.+)", text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "unknown", text
