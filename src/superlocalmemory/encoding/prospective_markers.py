# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Does this sentence describe something that has not happened yet?

A prospective memory is a plan: a deadline, an appointment, a scheduled event,
something with a date still ahead of it. It is a different kind of memory from
a record of what happened, and it is meant to be small and precise — the
handful of things a user is waiting on.

WHY THIS MODULE EXISTS

Two different regexes used to answer this question, one in ``fact_extractor``
and one in ``type_router``, and they disagreed. Measured on a real 468 MB
store: 869 facts carried the prospective type and **7** contained any planning
language at all. The rest were session summaries and records of completed work.

The extractor's pattern contained ``by \\w+``, which matches every passive-voice
and attribution sentence ever written — "was caused **by a** stale DNS entry" —
and ``yesterday``, which cannot indicate a plan. The router's contained bare
``ends?``, so "the service ends at midnight" was a plan.

WHAT REPLACED THEM

Two tiers, because the evidence really is of two strengths:

*Definite* markers point forward on their own. "Tomorrow" is tomorrow whatever
else the sentence says, and a deadline is a deadline.

*Circumstantial* markers — a weekday, a clock time, "starts", "in three days" —
are shaped like a plan but read equally well as narration. They count only when
nothing in the sentence says the thing already happened.

The asymmetry is deliberate. A plan wrongly filed as an ordinary memory is
still found by every other channel and costs a user nothing. An ordinary
memory wrongly filed as a plan pollutes the one list a user checks to see what
is coming up. **When in doubt, it is not a plan.**
"""

from __future__ import annotations

import re

__all__ = [
    "DEFINITE_FUTURE",
    "CIRCUMSTANTIAL_FUTURE",
    "ALREADY_HAPPENED",
    "looks_prospective",
]

_WEEKDAY = r"(?:mon|tues?|wednes|thurs?|fri|satur|sun)day"
_MONTH = (
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
)

#: Forward-looking on its own, whatever else the sentence contains.
DEFINITE_FUTURE = re.compile(
    r"\b(?:"
    r"deadline|due\s+(?:date|by|on)|"
    r"expir(?:es|ing|e)\s+(?:on|at|in|after)|expir(?:y|ation)\s+date|"
    r"scheduled?\s+(?:for|on|at|to)|reschedul(?:e|ed|ing)|"
    r"appointment|"
    r"upcoming|"
    r"tomorrow|"
    r"next\s+(?:week|month|year|quarter|sprint|release|" + _WEEKDAY + r")|"
    r"this\s+(?:coming|weekend)|"
    r"by\s+(?:end\s+of|eod|eow|cob|" + _WEEKDAY + r"|" + _MONTH + r"|\d{4}-\d{2}-\d{2})|"
    r"plans?\s+to|planning\s+to|"
    r"go[-\s]?live|cut[-\s]?over|launch\s+date|"
    r"will\s+(?:be|happen|start|begin|run|ship|land|expire|need|have)|"
    r"going\s+to\s+\w+"
    r")\b",
    re.IGNORECASE,
)

#: Shaped like a plan, but reads equally well as narration.
CIRCUMSTANTIAL_FUTURE = re.compile(
    r"\b(?:"
    r"on\s+" + _WEEKDAY + r"|"
    r"at\s+\d{1,2}:\d{2}|"
    r"in\s+\d+\s+(?:days?|weeks?|months?|years?)|"
    r"starts?\s+(?:on|at)|begins?\s+(?:on|at)|"
    r"meeting\s+(?:on|at)"
    r")\b",
    re.IGNORECASE,
)

#: Says the thing has already happened. Vetoes a circumstantial match.
ALREADY_HAPPENED = re.compile(
    r"\b(?:"
    r"yesterday|"
    r"last\s+(?:night|week|month|year|" + _WEEKDAY + r")|"
    r"\d+\s+(?:days?|weeks?|months?|years?)\s+ago|ago|"
    r"already|"
    r"was|were|had\s+been|has\s+been|have\s+been|"
    r"did|done|"
    r"complet(?:e|ed)|finish(?:ed)?|"
    r"shipped|released|landed|merged|deployed|"
    r"fixed|resolved|closed|"
    r"turned\s+out|ended|began|begun|started|"
    r"produced|caused|created|caught|found|"
    r"decided|agreed|discussed|reviewed|reported|"
    r"ran|wrote|built|took|made|went|came|said|led|"
    r"failed|passed|broke|resulted"
    r")\b",
    re.IGNORECASE,
)


def looks_prospective(text: str) -> bool:
    """True when the text describes something still ahead.

    A definite marker is enough on its own — "rescheduled to next Tuesday" is a
    plan even though "rescheduled" reads as past. A circumstantial one counts
    only when nothing says the thing already happened.
    """
    if not text:
        return False
    if DEFINITE_FUTURE.search(text):
        return True
    if CIRCUMSTANTIAL_FUTURE.search(text):
        return not ALREADY_HAPPENED.search(text)
    return False
