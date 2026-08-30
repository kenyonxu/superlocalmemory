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
    "ANCHORED_FUTURE",
    "PLANNING_LANGUAGE",
    "CIRCUMSTANTIAL_FUTURE",
    "RECURRING",
    "ALREADY_HAPPENED",
    "CALLED_OFF",
    "PLACEMENT",
    "RESCHEDULING",
    "DEFINITE_FUTURE",
    "looks_prospective",
]

_WEEKDAY = r"(?:mon|tues?|wednes|thurs?|fri|satur|sun)day"
_MONTH = (
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
)

#: Points forward on its own, whatever else the sentence says. Every member
#: carries an explicit forward time anchor, so "rescheduled to next Friday" is
#: still a plan even though "rescheduled" reads as past.
ANCHORED_FUTURE = re.compile(
    r"\b(?:"
    r"tomorrow|"
    r"next\s+(?:week|weekend|month|year|quarter|sprint|release|" + _WEEKDAY + r")|"
    r"this\s+(?:coming|weekend)|"
    r"upcoming\s+(?:release|meeting|week|month|sprint|deadline|event|launch|"
    r"call|review|milestone)|"
    r"by\s+(?:end\s+of|eod|eow|cob|" + _WEEKDAY + r"|" + _MONTH + r"|\d{4}-\d{2}-\d{2})|"
    r"due\s+(?:date|by|on|" + _WEEKDAY + r"|" + _MONTH + r")"
    r")\b",
    re.IGNORECASE,
)

#: The vocabulary of planning. These words are ABOUT something being planned,
#: and they read exactly as well in the past: a deadline can be missed, an
#: appointment cancelled, a launch date long gone. They count only when nothing
#: says the thing already happened.
#:
#: Treating them as definite is what re-polluted the list this module exists to
#: clean — "the appointment was cancelled yesterday" was filed as something to
#: look forward to.
PLANNING_LANGUAGE = re.compile(
    r"\b(?:"
    r"deadline|"
    r"appointment|"
    r"before\s+(?:" + _WEEKDAY + r"|" + _MONTH + r"|\d{4}-\d{2}-\d{2})|"
    r"expir(?:es|ing|e)\s+(?:on|at)\s+(?:" + _WEEKDAY + r"|" + _MONTH + r"|\d)|"
    r"expir(?:y|ation)\s+date|"
    r"scheduled?\s+(?:for|on|at|to)|reschedul(?:e|ed|ing)|"
    r"plans?\s+to|planning\s+to|"
    r"go[-\s]?live|cut[-\s]?over|launch\s+date|"
    r"will\s+(?:happen|start|begin|expire|ship|launch|resume|reopen|take\s+place)|"
    r"will\s+be\s+(?:deleted|removed|released|deployed|held|published|migrated|"
    r"archived|retired|rotated|decommissioned|shut\s+down)|"
    r"going\s+to\s+(?:happen|ship|start|launch|begin)"
    r")\b",
    re.IGNORECASE,
)

#: Kept for callers that imported the old name; the two tiers together are what
#: it used to mean.
DEFINITE_FUTURE = ANCHORED_FUTURE

#: Shaped like a plan, but reads equally well as narration.
CIRCUMSTANTIAL_FUTURE = re.compile(
    r"\b(?:"
    r"on\s+" + _WEEKDAY + r"|"
    r"(?:is|are)\s+(?:this\s+)?" + _WEEKDAY + r"|"
    r"this\s+(?:week|" + _WEEKDAY + r")|"
    r"at\s+\d{1,2}:\d{2}|"
    r"in\s+\d+\s+(?:days?|weeks?|months?|years?)|"
    r"(?:starts?|begins?|resumes?|reopens?)\s+(?:on|at)|"
    r"(?:on|at)\s+\d{4}-\d{2}-\d{2}|"
    r"meeting\s+(?:on|at)"
    r")\b",
    re.IGNORECASE,
)

#: A thing that happens on a cycle is not a thing that is coming up. "Standup at
#: 09:30 every day" belongs in nobody's list of what to expect this week.
RECURRING = re.compile(
    r"\b(?:every|each)\s+(?:day|week|month|morning|afternoon|evening|night|"
    r"sprint|release|" + _WEEKDAY + r")|\b(?:daily|weekly|monthly|nightly|"
    r"hourly|per\s+request|recurring)\b",
    re.IGNORECASE,
)

#: Verbs that PUT an event on a date, rather than report on one. "Rescheduled
#: to next Friday" and "was scheduled for next Monday" are both still plans;
#: "discussed the deadline for next month" is a record of a conversation. All
#: three are past-tense sentences containing a forward date, and only the first
#: two are something to look forward to — so a forward anchor overrides the past
#: only when one of these is what made the sentence past.
#:
#: The passive forms matter more than they look: people write notes that way
#: ("the appointment was booked for Tuesday"), and leaving them out missed a
#: whole class of ordinary phrasing.
PLACEMENT = re.compile(
    r"\b(?:reschedul(?:e|ed|ing)|mov(?:e|ed|ing)\s+to|postpon(?:e|ed|ing)|"
    r"push(?:ed)?\s+(?:to|back)|shift(?:ed)?\s+to|defer(?:red)?\s+to|"
    r"brought\s+forward|pull(?:ed)?\s+forward|bumped\s+to|"
    r"(?:scheduled?|booked|planned|slated|arranged|set|pencilled|penciled)\s+"
    r"(?:for|on|at|to)|"
    r"extend(?:ed)?\s+to|mov(?:e|ed)\s+forward\s+to|"
    r"chang(?:e|ed)\s+(?:the\s+)?(?:\w+\s+)?"
    r"(?:date|deadline|time|schedule|window)\s+to)\b",
    re.IGNORECASE,
)

#: The old name, kept because it reads in the one place it is used.
RESCHEDULING = PLACEMENT

#: Says the thing has already happened. Vetoes anything but an anchored future
#: that was anchored BY a rescheduling.
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
    r"fixed|resolved|closed|cancelled|canceled|missed|happened|expired|"
    r"turned\s+out|ended|began|begun|started|"
    r"produced|caused|created|caught|found|"
    r"updated|changed|edited|modified|renamed|bumped|committed|"
    r"restarted|configured|installed|"
    r"had|met|arrived|attended|ate|spoke|talked|visited|saw|"
    # deleted / removed / added are deliberately absent: each is both a simple
    # past AND the participle in a future passive, and "the worktrees will be
    # deleted" is a plan. The future-passive list in PLANNING_LANGUAGE is the
    # narrower half of that ambiguity and it is the half worth keeping.
    r"decided|agreed|discussed|reviewed|reported|"
    r"ran|wrote|built|took|made|went|came|said|led|"
    r"failed|passed|broke|resulted"
    r")\b",
    re.IGNORECASE,
)


#: The event is off. This outranks everything, including a present-tense clause
#: saying when it was going to be.
CALLED_OFF = re.compile(
    r"\b(?:cancel(?:led|ed|s)?|called\s+off|scrapped|abandoned|"
    r"dropped|shelved|withdrawn|no\s+longer\s+(?:happening|planned|scheduled))\b",
    re.IGNORECASE,
)


#: A present-tense statement that something is on a future date: "the launch is
#: next Friday", "the freeze starts Monday", "the review is this Tuesday".
#: Present tense about a forward date is a plan whatever else the surrounding
#: sentence reports having done.
#:
#: Every part of this is bounded on both sides. A first attempt was not, and it
#: matched "is dec" inside "is decision", "are mar" inside "are marked", and
#: "this a" inside "is this a" — turning 14 memories into 72 on a real store.
#: An unbounded month abbreviation is a trap in any prose.
_FUTURE_FACT = re.compile(
    r"\b(?:is|are|starts?|begins?|opens?|closes?|resumes?|reopens?|runs?|"
    r"lands?|ships?|goes\s+live|kicks\s+off)\s+"
    r"(?:on\s+|at\s+)?"
    r"(?:"
    r"tomorrow"
    r"|next\s+(?:week|weekend|month|year|quarter|sprint|release|" + _WEEKDAY + r")"
    r"|this\s+(?:coming\s+)?(?:week|weekend|" + _WEEKDAY + r")"
    r"|" + _WEEKDAY +
    r"|\d{4}-\d{2}-\d{2}"
    r")\b",
    re.IGNORECASE,
)


def _states_a_future_fact(text: str) -> bool:
    """Present tense about a forward date, inside any sentence."""
    return bool(_FUTURE_FACT.search(text))


def looks_prospective(text: str) -> bool:
    """True when the text describes something still ahead.

    Three tiers, because the evidence really is of three strengths. An anchored
    future — "next Tuesday", "by Friday", "on 2026-09-04" — points forward
    whatever else the sentence says. Planning vocabulary and circumstantial
    wording both read equally well in the past, so they count only when nothing
    says the thing already happened. Anything on a cycle is not a plan at all.
    """
    if not text:
        return False
    behind = ALREADY_HAPPENED.search(text)
    if CALLED_OFF.search(text):
        # "The deadline is next Friday but was cancelled yesterday" reads as a
        # future fact and is not one. Cancelling is the one thing that beats a
        # present-tense statement about a forward date.
        return False
    if _states_a_future_fact(text):
        # "I updated the timeline so the launch IS next Friday" — the past-tense
        # verb is about the note, and the clause with the date is in the present
        # about something ahead. A veto that scans the whole sentence cannot see
        # that difference and drops a real deadline.
        return True
    if ANCHORED_FUTURE.search(text):
        # Checked BEFORE the recurring veto. "The weekly review is next Tuesday"
        # names one dated occurrence of a recurring thing, and that occurrence
        # is coming up; "weekly" describes the series, not this instance.
        # A forward anchor beats the past only when the past word is what moved
        # the event. "Rescheduled to next Friday" is a plan; "discussed the
        # deadline for next month" is a record of a conversation.
        return not behind or bool(RESCHEDULING.search(text))
    if RECURRING.search(text):
        return False
    if PLANNING_LANGUAGE.search(text) or CIRCUMSTANTIAL_FUTURE.search(text):
        return not behind
    return False
