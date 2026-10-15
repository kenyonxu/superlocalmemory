# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Resolve relative source expressions with an explicit event date only."""

from __future__ import annotations

from superlocalmemory.encoding.typed_values import _RELATIVE_RE


def relative_dates(source: str, reference_date: str | None) -> set[str]:
    if not reference_date:
        return set()
    from dateutil.parser import ParserError, parse

    try:
        parse(reference_date)
    except (ParserError, ValueError, OverflowError):
        return set()
    from superlocalmemory.encoding.temporal_parser import TemporalParser

    parser = TemporalParser(reference_date)
    dates = set()
    for match in _RELATIVE_RE.finditer(source):
        date = parser.extract_dates_from_text(match.group(0)).get("referenced_date")
        if date:
            dates.add(date[:10])
    return dates
