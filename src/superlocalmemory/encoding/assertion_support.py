# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Conservative assertion support checks, independent of the generating model.

New content words cannot be licensed by the model's confidence. Quote text
must remain verbatim; names must appear in the source; the best matching
speaker span must support the subject. Uncertain paraphrases are withheld.
"""

from __future__ import annotations

import re

from superlocalmemory.encoding.speaker_spans import normalized_spans, quoted_texts

_FUNCTION_WORDS = frozenset(
    "a an the user users i my me myself we our us he she it they "
    "his her its their this that these those is are was were be been "
    "am has have had do does did will would should can could may "
    "might must to of in on at by for from with and or as than "
    "over under into there only all every each before after prior ahead followed".split()
)
# Safe inflection and unit aliases. Never map antonyms or preference to usage.
_ALIASES = {
    "uses": "use",
    "used": "use",
    "using": "use",
    "works": "work",
    "preferred": "preference",
    "prefers": "preference",
    "prefer": "preference",
    "liked": "preference",
    "likes": "preference",
    "loves": "love",
    "seconds": "s",
    "second": "s",
    "minutes": "min",
    "milliseconds": "ms",
    "records": "record",
    "nodes": "node",
    "meetings": "meeting",
    "starts": "start",
    "started": "start",
    "runs": "run",
    "running": "run",
    "needs": "need",
    "loads": "load",
    "dropped": "drop",
    "fell": "drop",
    "monthly": "month",
    "costs": "cost",
    "holds": "store",
    "hit": "reach",
    "reached": "reach",
    "requests": "request",
    "req": "request",
    "building": "build",
    "froze": "freeze",
    "frozen": "freeze",
    "begins": "start",
    "backup": "back",
    "comes": "come",
    "written": "write",
    "hosting": "host",
    "hosted": "host",
    "current": "currently",
    "publishing": "publish",
    "failed": "fail",
    "uploads": "upload",
    "retried": "retry",
    "daily": "day",
    "arrived": "added",
    "meets": "meeting",
    "meet": "meeting",
    "covers": "cover",
    "rotated": "rotate",
    "issue": "bug",
    "release": "version",
    "ships": "ship",
    "shipping": "ship",
    "approving": "approval",
    "approved": "approval",
    "fixed": "fix",
    "relies": "use",
    "postgresql": "postgres",
    "renewed": "renew",
    "renewal": "renew",
    "took": "take",
    "takes": "take",
    "recalled": "recall",
    "happened": "happen",
    "shipped": "ship",
}
_NEGATIVE_EQUIVALENTS = frozenset(
    {
        "disabled",
        "disallowed",
        "unsupported",
        "unavailable",
        "private",
        "requires",
        "weekdays",
        "passed",
    }
)


def words(text: str) -> set[str]:
    tokens = re.findall(r"[\w]+(?:['’]s)?", text.casefold())
    return {
        _ALIASES.get(
            t.removesuffix("'s").removesuffix("’s"), t.removesuffix("'s").removesuffix("’s")
        )
        for t in tokens
        if t not in _FUNCTION_WORDS and not t.isdigit()
    }


def assertion_reasons(fact: str, source: str) -> list[str]:
    normalized = normalized_spans(source)
    from superlocalmemory.encoding.source_spans import exact_span

    if any(exact_span(fact.strip(), span) is not None for span in normalized):
        return []
    spans = [
        piece
        for span in normalized
        for piece in re.split(r"(?<=[.!?])\s+|\n+|\bbut\b|\bwhereas\b", span)
        if piece.strip()
    ]
    support = " ".join(spans)
    reasons = []
    quotes = quoted_texts(fact)
    if any(q not in quoted_texts(source) for q in quotes):
        reasons.append("attribution_changed")
    named_text = _without_dates(fact)
    if _adds_entity(named_text, support, source):
        reasons.append("unsupported_entity")
    undated = named_text
    fact_words = words(undated)
    if not fact_words:
        return reasons
    best = max(spans, key=lambda s: len(words(s) & fact_words), default="")
    missing = fact_words - words(best)
    if re.search(r"\bwe\b", source, re.I):
        missing.discard("team")
    if re.search(r"\b(?:last|next|this) year\b", source, re.I):
        missing.discard("year")
    from superlocalmemory.encoding.source_fidelity import _NEGATION_RE

    negative_aliases = _NEGATIVE_EQUIVALENTS if _NEGATION_RE.search(source) else set()
    if missing - negative_aliases - _supported_paraphrase_words(fact, source):
        reasons.append("unsupported_assertion")
    if _subject_changed(fact, best) or relation_changed(fact, source):
        reasons.append("attribution_changed")
    return reasons


def _subject_changed(fact: str, span: str) -> bool:
    """A named/user subject may not migrate from another speaker or object."""
    head = re.match(r"(?:[Tt]he user|[A-Z][\w]*(?: [A-Z][\w]*)*)(?:['’]s)?", fact)
    if re.match(r"the user\b", fact, re.I):
        return re.search(r"\bthe user\b", span, re.I) is None
    if not head or head.group(0).split()[0] in {
        "The",
        "A",
        "An",
        "In",
        "On",
        "At",
        "All",
        "Every",
        "Each",
        "There",
        "Port",
        "Issue",
        "Release",
        "Building",
        "Publishing",
        "Hosting",
        "Uptime",
    }:
        return False
    subject = head.group(0).lower().replace("’", "'")
    # A possessive absent from the supporting span changes ownership.
    if subject.endswith("'s") and subject not in span.lower().replace("’", "'"):
        return True
    if words(subject) <= words(span):
        return False
    return re.search(r"(?<!\w)" + re.escape(subject) + r"(?!\w)", span.lower()) is None


def _supported_paraphrase_words(fact: str, source: str) -> set[str]:
    """Small audited aliases; this is not a model's permission to improvise."""
    generic = {
        "take",
        "happen",
        "time",
        "where",
        "target",
        "due",
        "code",
        "come",
        "about",
        "more",
        "team",
        "currently",
    }
    if "per" in source.lower() or " a day" in source.lower() or "/" in source:
        generic.update({"per", "daily"})
    if re.search(r"\d", source):
        generic.update({"request", "req", "ram", "need", "monthly"})
    return generic


def relation_changed(fact: str, source: str) -> bool:
    """Detect explicit subject/object swaps and movement of a negated object."""
    normalized = " ".join(normalized_spans(source))
    relation = re.compile(
        r"\b([A-Z][\w]*(?: [A-Z][\w]*)*)\s+"
        r"(manages?|owns?|leads?|reports? to)\s+([A-Z][\w]*)"
    )
    source_relations = {
        (a.lower(), verb.lower().removesuffix("s"), b.lower())
        for a, verb, b in relation.findall(normalized)
    }
    for a, verb, b in relation.findall(fact):
        signature = (a.lower(), verb.lower().removesuffix("s"), b.lower())
        if source_relations and signature not in source_relations:
            return True
    negated = re.compile(r"\bnot\s+(?:to\s+|the\s+|a\s+|an\s+)?([\w]+)", re.I)
    negatives = {m.lower() for m in negated.findall(normalized)}
    return bool(negatives and any(m.lower() not in negatives for m in negated.findall(fact)))


def _without_dates(fact: str) -> str:
    from superlocalmemory.encoding.source_fidelity import _fact_dates

    for written, *_ in _fact_dates(fact):
        fact = fact.replace(written, " ")
    return fact


def _adds_entity(named_text: str, support: str, source: str) -> bool:
    starters = {
        "The",
        "A",
        "An",
        "I",
        "In",
        "On",
        "At",
        "For",
        "Every",
        "Each",
        "All",
        "There",
        "Port",
        "Uptime",
    }
    for name in re.findall(r"\b[A-Z][\w]*(?:[ -][A-Z][\w]*)*", named_text):
        if name in starters or words(name) <= words(support) | _supported_paraphrase_words(
            named_text, source
        ):
            continue
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", support, re.I) is None:
            return True
    return False
