# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Parse each absolute calendar span once, keeping the most precise pattern."""

from __future__ import annotations


def absolute_dates(text, patterns, parse):
    dates, seen = [], []
    for pattern in patterns:
        for match in pattern.finditer(text):
            if any(start < match.end() and match.start() < end for start, end in seen):
                continue
            date = parse(match.group(0))
            if date is not None:
                dates.append(date)
                seen.append(match.span())
    return dates
