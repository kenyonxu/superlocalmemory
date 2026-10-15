# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""When two entity names may be treated as one: the comparison rules.

WHY. The resolver used to merge a new name into any canonical name OR ALIAS
whose whole-string Jaro-Winkler score was at least 0.85, and the alias it then
wrote became a target for the next name. Merges chained: on the author's store
one entity ("Lost") collected 145 aliases (Cost, Post, Last, PostgreSQL,
Podcasts, ...) and 71% of the entity links on freshly written notes were wrong.
Whole-string Jaro-Winkler also cannot see what makes two names different:
"AgentAssert"/"AgentAssay" (0.905), "SLM 4.1.24"/"SLM 4.1.25" (0.960) and
"Claude Code"/"Claude Opus" (0.891) all scored as the same thing.

THE RULES, strongest evidence first, each one a separate level the way
record-linkage comparisons are built (Splink's comparison levels go from an
exact match through tailored fuzzy thresholds to "all other comparisons", and
the definition of similarity is chosen per kind of field: a date is not
compared like a first name):

1. ``SAME`` "normalized": the names are equal after case folding, accent
   removal, punctuation/space/hyphen removal, CamelCase and letter/digit
   splitting, a possessive "'s" and a leading "the" ("Wi-Fi" = "WiFi",
   "Super Local Memory" = "SuperLocalMemory", "GPT-4" = "GPT4").
2. ``DIFFERENT`` whenever the numbers differ. Versions and numbers are compared
   exactly, never by spelling ("4.1.24" != "4.1.25", "Python 3.12" !=
   "Python 3.14").
3. Token-level comparison, never whole-string. Cohen, Ravikumar and Fienberg
   ("A Comparison of String Distance Metrics for Name-Matching Tasks", 2003)
   note the Jaro metrics seem "intended primarily for short strings", and found
   token-based schemes that compare tokens individually (SoftTFIDF with a
   Jaro-Winkler secondary distance) best overall. Here the names must have the
   same tokens except one, and that one token may differ only by a typo:
   ``SAME`` "typo" for one inserted, deleted or transposed character in a token
   of five or more letters with the same first letter ("Kubernets",
   "Alice Smitth"); a single substituted letter only from nine letters up.
   Anything else between differing tokens is ``DIFFERENT`` ("Assert"/"Assay",
   "Code"/"Opus"); a differing token count is ``DIFFERENT`` too ("Qualixar" vs
   "Qualixar OS", "Claude" vs "Claude Code").
4. ``BORDERLINE`` for the cases a rule cannot settle and a person could: a
   one-letter substitution in a short token ("Smith"/"Smyth"), an acronym of
   the other name's words ("SLM"/"Super Local Memory"), and a single word that
   extends the other by a short suffix ("Postgres"/"PostgreSQL"). Mode A never
   merges these (a wrong merge corrupts every link through the entity; a missed
   one only leaves two entities). Mode B/C may ask the local model about the
   pair.

Fuzzy rules are applied only to canonical names, never to aliases, so a name
can only ever be one typo away from the name of the entity it joins: nothing
chains. Aliases are used for exact lookups only (``alias_in_use``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

SAME = "same"
BORDERLINE = "borderline"
DIFFERENT = "different"

#: Alias sources written before 4.1.25: the whole-string fuzzy merge
#: ("jaro_winkler", may be a link of a chain) and the free-form model mapping
#: ("llm"). ``slm db repair-entities`` re-checks them and relabels the ones it
#: keeps; aliases a model confirms from 4.1.25 on are "llm_confirmed".
LEGACY_ALIAS_SOURCES: frozenset[str] = frozenset({"jaro_winkler", "llm"})


def alias_in_use(source: object, alias: str, canonical: str) -> bool:
    """Whether an exact lookup may use this alias (fuzzy rules never do).

    An old fuzzy-merge alias is used only when the rules would merge it today
    (a typo of the entity's name). One they reject or leave to a model ("Plot"
    and "Post" on "Lost", "AgentAssay" on "AgentAssert") was merged with no
    confirmation, and is exactly what the repair removes. Every other alias, the old model's
    included, keeps working until the repair has re-checked it, so genuine
    aliases do not vanish between the upgrade and the repair.
    """
    if str(source or "") != "jaro_winkler":
        return True
    return compare(str(alias or ""), str(canonical or ""))[0] == SAME


_CAMEL_1 = re.compile(r"([a-z])([A-Z])")
_CAMEL_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_LETTER_DIGIT = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_POSSESSIVE = re.compile(r"['’]s\b", re.IGNORECASE)
_TOKEN = re.compile(r"\d+(?:\.\d+)+|[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class NameKey:
    tokens: tuple[str, ...]
    compact: str
    numbers: tuple[str, ...]
    spaced: bool  # the written name had more than one word


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=65536)
def name_key(name: str) -> NameKey:
    """The comparison key of a written name (cached: names repeat a lot)."""
    text = _POSSESSIVE.sub("", _fold(name or "").strip())
    text = _CAMEL_2.sub(r"\1 \2", _CAMEL_1.sub(r"\1 \2", text))
    text = _LETTER_DIGIT.sub(" ", text).lower()
    tokens = _TOKEN.findall(text)
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    numbers = tuple(t for t in tokens if any(c.isdigit() for c in t))
    return NameKey(tuple(tokens), "".join(tokens), numbers, len((name or "").split()) > 1)


def edit_kind(a: str, b: str) -> str | None:
    """The single edit turning ``a`` into ``b`` ('indel', 'swap', 'sub'), or
    None when they are equal or more than one edit apart."""
    if a == b or abs(len(a) - len(b)) > 1:
        return None
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return "sub"
        if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] \
                and a[diff[1]] == b[diff[0]]:
            return "swap"
        return None
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return "indel"
    return None


def _token_verdict(a: str, b: str) -> tuple[str, str]:
    """Two differing tokens of otherwise equal names."""
    kind = edit_kind(a, b)
    if kind is None:
        return DIFFERENT, "distinct word"
    shortest = min(len(a), len(b))
    if a[0] != b[0] or shortest < 5:
        return BORDERLINE, "near spelling of a short word"
    if kind in ("indel", "swap") or shortest >= 9:
        return SAME, "typo"
    return BORDERLINE, "one letter differs"


def _is_acronym(short: NameKey, long_: NameKey) -> bool:
    if len(short.tokens) != 1 or len(long_.tokens) < 2 or long_.numbers:
        return False
    word = short.tokens[0]
    return 2 <= len(word) <= 6 and word.isalpha() and word == "".join(
        t[0] for t in long_.tokens)


def _is_suffix_extension(a: NameKey, b: NameKey) -> bool:
    """One single written word extends the other by a short suffix."""
    if a.spaced or b.spaced or a.numbers or b.numbers:
        return False
    short, long_ = sorted((a.compact, b.compact), key=len)
    return len(short) >= 5 and long_.startswith(short) and len(long_) - len(short) <= 4


@lru_cache(maxsize=65536)
def compare(mention: str, canonical: str) -> tuple[str, str]:
    """(verdict, reason) for treating ``mention`` as the entity ``canonical``."""
    a, b = name_key(mention), name_key(canonical)
    if not a.compact or not b.compact:
        return DIFFERENT, "empty name"
    if a.compact == b.compact:
        return SAME, "normalized"
    if a.numbers != b.numbers:
        return DIFFERENT, "numbers differ"
    if len(a.tokens) == len(b.tokens):
        diff = [(x, y) for x, y in zip(a.tokens, b.tokens) if x != y]
        if len(diff) == 1:
            verdict = _token_verdict(*diff[0])
            if verdict[0] != DIFFERENT or not _is_suffix_extension(a, b):
                return verdict
            return BORDERLINE, "longer form of the same word"
        return DIFFERENT, "several words differ"
    if _is_acronym(a, b) or _is_acronym(b, a):
        return BORDERLINE, "acronym"
    if _is_suffix_extension(a, b):
        return BORDERLINE, "longer form of the same word"
    return DIFFERENT, "different words"


_TITLE_WORD = re.compile(r"^[A-Z][a-z]+$")
_NAMED_TYPES = frozenset({"person", "organization", "place", "product"})


def plain_word_may_name(word: str, canonical: str, entity_type: str = "") -> bool:
    """Whether a lower-case question word may name this entity exactly.

    People type "what did alice say" and "when is priya's birthday". A single
    Capitalised name ("Alice", "Lost") is reached by its lower-case word
    unless that word is an everyday English word ("lost", "post", "plot":
    encoding/common_words), in which case only an entity typed as a person,
    organisation, place or product is. Other names ("pgbouncer", "Claude
    Code", "SuperLocalMemory") are always reachable: nobody writes them in
    lower case by accident.
    """
    from superlocalmemory.encoding.common_words import is_common_word

    if not canonical or not _TITLE_WORD.match(canonical.strip()):
        return bool(canonical)
    return not is_common_word(word) or entity_type in _NAMED_TYPES


class NameIndex:
    """Canonical names by comparison key, for the rules above without a scan.

    Every non-normalized rule needs the same first letter of the compact key
    (typos keep the first letter, an acronym starts with it, a longer form
    shares it), so candidates are bucketed by that letter. Rows keep their
    order: the first entity a rule accepts wins, as the scan's did.
    """

    __slots__ = ("_by_compact", "_buckets")

    def __init__(self, rows: list[tuple[str, str]] | None = None) -> None:
        self._by_compact: dict[str, str] = {}
        self._buckets: dict[str, list[tuple[str, str]]] = {}
        for entity_id, name in rows or ():
            self.add(entity_id, name)

    def add(self, entity_id: str, name: str) -> None:
        compact = name_key(name).compact
        if not compact:
            return
        self._by_compact.setdefault(compact, entity_id)
        self._buckets.setdefault(compact[0], []).append((entity_id, name))

    def best(self, mention: str) -> tuple[str, str | None, str, str]:
        """(verdict, entity_id, canonical_name, reason) for the best candidate."""
        compact = name_key(mention).compact
        if not compact:
            return DIFFERENT, None, "", "empty name"
        hit = self._by_compact.get(compact)
        if hit is not None:
            return SAME, hit, "", "normalized"
        border: tuple[str, str | None, str, str] = (DIFFERENT, None, "", "no candidate")
        for entity_id, name in self._buckets.get(compact[0], ()):
            verdict, reason = compare(mention, name)
            if verdict == SAME:
                return SAME, entity_id, name, reason
            if verdict == BORDERLINE and border[1] is None:
                border = (BORDERLINE, entity_id, name, reason)
        return border


__all__ = [
    "BORDERLINE", "DIFFERENT", "LEGACY_ALIAS_SOURCES", "NameIndex", "NameKey", "SAME",
    "alias_in_use", "compare", "edit_kind", "name_key", "plain_word_may_name",
]
