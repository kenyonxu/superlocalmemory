"""Conservative source-backed cause endpoints; a shared entity is not evidence."""
import re
from datetime import datetime, timezone

WINDOW_SECONDS = 7 * 86400
_BACKWARD = re.compile(r"\b(?:because(?: of)?|caused by|due to)\b", re.I)
_FORWARD = re.compile(r"\b(?:led to|resulted in|therefore|consequently|as a result)\b", re.I)
_UNASSERTED = re.compile(r"\b(?:not|never|might|may|would|could|if|unless)\b", re.I)
_STOP = {"the", "a", "an"}
_CLAUSE_BOUNDARY = re.compile(
    r"[.;:!?\n,]|\b(?:after|before|while|when|although|but|and|or|which)\b", re.I,
)


def _tokens(text):
    return [word for word in re.findall(r"\w+", text.casefold()) if word not in _STOP]


def cause_clause(text):
    """Extract an asserted cause span, abstaining on purpose/negation/hypothesis."""
    if _UNASSERTED.search(text):
        return None
    cue = _BACKWARD.search(text)
    if cue:
        return text[cue.end():].strip(" .,")
    cue = _FORWARD.search(text)
    return text[:cue.start()].strip(" .,") if cue else None


def _time(fact):
    raw = fact.referenced_date or fact.observation_date or fact.created_at
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, AttributeError):
        return None


def supports_cause(prior, effect, clause):
    """Require an ordered assertion span within a prior clause and one week.

    Articles may differ, but predicates and word order must match. Scattered
    vocabulary (an audit passed after a rehearsal failed) is not an assertion
    of audit failure. Unsupported paraphrases conservatively abstain.
    """
    cause_time, effect_time = _time(prior), _time(effect)
    if cause_time is None or effect_time is None or _UNASSERTED.search(prior.content):
        return False
    elapsed = (effect_time - cause_time).total_seconds()
    words = _tokens(clause)
    if not 0 < elapsed <= WINDOW_SECONDS or len(words) < 2:
        return False
    for span in _CLAUSE_BOUNDARY.split(prior.content):
        observed = _tokens(span)
        if any(observed[i:i + len(words)] == words
               for i in range(len(observed) - len(words) + 1)):
            return True
    return False
