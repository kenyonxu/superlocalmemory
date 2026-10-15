# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Resolve a date string from memory text to ISO form — only if it is a date.

Moved out of ``fact_extractor`` (which re-exports ``_try_parse_date``) so the
guard sits next to the parser it protects. A fuzzy parser accepts almost any
number, so a string reaches it only when ``looks_like_calendar_date`` says it
names a date, a month, a weekday or a relative time. "2004.6 ms", "4.1.21",
"$12.50", "port 8765" and "#149" are measurements and stay measurements.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from superlocalmemory.encoding.typed_values import has_relative_time, looks_like_calendar_date

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_RELATIVE_DAYS: dict[str, int] = {
    "yesterday": -1,
    "today": 0,
    "tomorrow": 1,
    "the day before": -2,
    "the other day": -2,
    "day before yesterday": -2,
}


def _resolve_relative(raw_lower: str, reference_date: str) -> str | None:
    """Rule-based relative date resolution (no dateparser dependency)."""
    from dateutil import parser as du_parser

    ref_dt: datetime = du_parser.parse(reference_date)
    if raw_lower in _RELATIVE_DAYS:
        return (ref_dt + timedelta(days=_RELATIVE_DAYS[raw_lower])).date().isoformat()
    if raw_lower == "last week":
        return (ref_dt - timedelta(days=7)).date().isoformat()
    if raw_lower == "last month":
        month = ref_dt.month - 1 or 12
        year = ref_dt.year if ref_dt.month > 1 else ref_dt.year - 1
        return f"{year}-{month:02d}-{ref_dt.day:02d}"
    if raw_lower == "last year":
        return f"{ref_dt.year - 1}-{ref_dt.month:02d}-{ref_dt.day:02d}"
    if raw_lower == "next week":
        return (ref_dt + timedelta(days=7)).date().isoformat()
    if raw_lower == "next month":
        month = ref_dt.month + 1 if ref_dt.month < 12 else 1
        year = ref_dt.year if ref_dt.month < 12 else ref_dt.year + 1
        return f"{year}-{month:02d}-{ref_dt.day:02d}"
    return None


def _resolve_relative_expression(raw: str, reference_date: str | None) -> str | None:
    """A relative time resolved against the reference date (or today)."""
    try:
        if reference_date:
            resolved = _resolve_relative(raw.strip().lower(), reference_date)
            if resolved:
                return resolved
        from superlocalmemory.encoding.temporal_parser import TemporalParser

        found = TemporalParser(reference_date).extract_dates_from_text(raw)
        referenced = found.get("referenced_date")
        return referenced[:10] if referenced else None
    except Exception:
        return None


def try_parse_date(raw: str | None, reference_date: str | None = None) -> str | None:
    """Resolve a supported calendar value, preserving month precision. Never raises."""
    if not raw:
        return None
    direct = _direct_calendar(raw)
    if direct is not None:
        return direct
    if not looks_like_calendar_date(raw):
        return None

    if has_relative_time(raw) and not reference_date:
        return None
    if has_relative_time(raw):
        # Relative first: a fuzzy parser drops the words, so "next Tuesday"
        # came back as today and "in 2 weeks" as the 2nd of this month.
        resolved = _resolve_relative_expression(raw, reference_date)
        if resolved:
            return resolved

    try:
        from dateutil import parser as du_parser

        default = du_parser.parse(reference_date) if reference_date else datetime(2000, 1, 1)
        return du_parser.parse(raw, fuzzy=True, default=default).date().isoformat()
    except Exception:
        pass

    try:
        import dateparser

        settings: dict[str, Any] = {"PREFER_DATES_FROM": "past"}
        if reference_date:
            ref = dateparser.parse(reference_date)
            if ref:
                settings["RELATIVE_BASE"] = ref
        result = dateparser.parse(raw, settings=settings)
        if result:
            return result.date().isoformat()
    except ImportError:
        pass
    except Exception:
        pass

    return None


_DATE_FIELDS = ("referenced_date", "interval_start", "interval_end")


def keep_supported_dates(facts: list, source_text: str, reference_date: str | None = None) -> list:
    """Model-written facts with every date field the source does not support cleared.

    A model asked to resolve "yesterday" also answers with dates nobody
    stated — the 4.1.21 local model gave "2004.6 ms" the date 2004-06-01. A
    date field is kept only when the source states it, reformats it, names
    its month or says something relative ("yesterday") it resolves. The fact
    text is not touched here; the write path checks that separately.
    """
    from dataclasses import replace

    from superlocalmemory.encoding.source_fidelity import date_is_supported

    kept = []
    for fact in facts:
        cleared = {
            name: None
            for name in _DATE_FIELDS
            if getattr(fact, name, None)
            and not date_is_supported(getattr(fact, name), source_text, reference_date)
        }
        kept.append(replace(fact, **cleared) if cleared else fact)
    return kept


__all__ = ["keep_supported_dates", "try_parse_date"]


def month_fields(text: str) -> dict:
    """A month/year denotes a range, not an invented day inside that month."""
    import calendar

    from superlocalmemory.encoding.typed_values import _MONTH

    found = re.search(rf"\b({_MONTH})\s+((?:19|20)\d{{2}})\b", text, re.I)
    if not found:
        return {}
    if re.search(r"\d{1,2}(?:st|nd|rd|th)?\s+$", text[: found.start()], re.I):
        return {}
    from dateutil.parser import parse

    date = parse(found.group(0), default=datetime(2000, 1, 1))
    month = date.strftime("%Y-%m")
    last = calendar.monthrange(date.year, date.month)[1]
    return {
        "referenced_date": month,
        "interval_start": month + "-01T00:00:00",
        "interval_end": f"{month}-{last:02d}T23:59:59",
    }


def _direct_calendar(raw: str) -> str | None:
    if _ISO_RE.match(raw.strip()):
        return raw.strip()
    monthly = month_fields(raw)
    if monthly and re.fullmatch(r"[A-Za-z.]+\s+\d{4}", raw.strip()):
        return monthly["referenced_date"]
    if re.fullmatch(r"\d{4}-\d{2}", raw.strip()):
        try:
            datetime.fromisoformat(raw.strip() + "-01")
            return raw.strip()
        except ValueError:
            return None
    return None
