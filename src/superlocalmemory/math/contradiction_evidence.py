"""Semantic admission for the experimental pairwise sheaf residual.

SLM arXiv:2603.14588's H1 claim does not make angular difference a logical
contradiction. No cohomology formula is changed here: require explicit slots
before returning a factual conflict. Unknown statements abstain.
"""
import re

_SLOT = re.compile(
    r"^(.+?)'s (status|age|birthday|salary|color|database|favorite \w+) (?:is|equals) (.+)$",
    re.I,
)
_NEG = re.compile(r"\b(?:does|do|did) not (\w+)|\b(is|are|was|were) not\b", re.I)


def _positive(text):
    normalized = text.casefold().strip(" .")
    neg = _NEG.search(normalized)
    if neg:
        verb = neg[1] or neg[2]
        if neg[1] and not verb.endswith("s"):
            verb += "s"
        return normalized[:neg.start()] + verb + normalized[neg.end():], True
    return normalized, False


def incompatible(left, right):
    """Same subject and scalar attribute, or explicit assertion/negation pair."""
    # Separate observations about different event dates can be valid updates.
    for field in ("referenced_date",):
        a, b = getattr(left, field, None), getattr(right, field, None)
        if a and b and a != b:
            return False
    a, na = _positive(left.content)
    b, nb = _positive(right.content)
    if a == b and na != nb:
        return True
    sa, sb = _SLOT.fullmatch(a), _SLOT.fullmatch(b)
    return bool(sa and sb and sa.groups()[:2] == sb.groups()[:2]
                and sa[3] != sb[3] and not na and not nb)
