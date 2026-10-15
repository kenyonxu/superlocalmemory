# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A synthetic agent-memory store for measuring "answer or abstain".

The store is shaped like a real agent's memory after months of use: a minority
of human-readable notes (decisions, facts, events, code notes, versions, names,
a few non-English sentences, paraphrase pairs) buried in a majority of
ID-heavy rows -- receipts, logs, session-ended stubs, commit lines and tool
events full of hex ids, UUIDs and app ids. Those ID-heavy rows are the "hubs":
their embeddings sit close to everything, so random text reaches them with a
high cosine. A store without them cannot show the failure this measures.

It is also unevenly described, like a store that is a year old. About 40% of
it is a "legacy" slice written before tags, projects and observation dates
were recorded: no tags, no project, no session date, a rule-guessed kind. The
rest is "described": tags, a project and a date on every row. About a quarter
of all rows are tiny stubs under 40 characters ("[] session ended ...",
"rcpt asdk_app_... ok"). Lifecycle states are spread like a real store's
(about 26% active, 58% warm, 14% cold, 2% archived; questions never point at
an archived row). Every measurement is reported per slice, because a design
that only works when tags, kinds and dates exist is not a design.

Everything here is synthetic. The hand-written rows that questions point at
live in ``tests/fixtures/answerability/anchors.jsonl``; this module adds the
deterministic filler around them. Same code, same rows, same order, on every
machine: the generator uses only ``hashlib`` so no library or interpreter
version can change the store under a recorded baseline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

#: Bump when the generated rows change; recorded baselines name the version.
CORPUS_VERSION = "answerability-corpus-v2"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "answerability"

_FILLER_COUNTS = {
    "receipt": 240,
    "log": 260,
    "session": 160,
    "commit": 120,
    "tool_event": 140,
    "meeting": 240,
    "maintenance": 220,
    "stub": 520,
}
LEGACY, DESCRIBED = "legacy", "described"
#: Share of rows in the legacy slice, in tenths (4 = 40%).
_LEGACY_TENTHS = 4


@dataclass(frozen=True)
class Memory:
    key: str
    content: str
    kind: str
    project: str
    session_date: str
    slice: str = DESCRIBED
    lifecycle: str = "active"

    @property
    def metadata(self) -> dict | None:
        """What the save carries: nothing at all for the legacy slice."""
        if self.slice == LEGACY:
            return None
        return {"project": self.project, "tags": self.kind}

    @property
    def stored_date(self) -> str | None:
        return None if self.slice == LEGACY else self.session_date


class _Stream:
    """Deterministic bytes from a label: sha256 in counter mode."""

    def __init__(self, label: str) -> None:
        self._label = label.encode("utf-8")
        self._counter = 0
        self._buffer = b""

    def _bytes(self, n: int) -> bytes:
        while len(self._buffer) < n:
            block = hashlib.sha256(self._label + self._counter.to_bytes(8, "big")).digest()
            self._buffer += block
            self._counter += 1
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def below(self, n: int) -> int:
        return int.from_bytes(self._bytes(4), "big") % n

    def pick(self, seq):
        return seq[self.below(len(seq))]

    def hex(self, n: int) -> str:
        return self._bytes((n + 1) // 2).hex()[:n]

    def alnum(self, n: int, alphabet: str = "0123456789ABCDEFGHJKMNPQRSTVWXYZ") -> str:
        return "".join(alphabet[b % len(alphabet)] for b in self._bytes(n))

    def uuid(self) -> str:
        h = self.hex(32)
        return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:32]}"


_PEOPLE = ("Ines Duarte", "Kwame Boateng", "Lena Fischer", "Ravi Menon", "Sofia Marino",
           "Hannah Weiss", "Diego Alvarez", "Yuki Sato", "Farah Haddad", "Piotr Nowak")
_COMPONENTS = ("gateway", "billing", "worker", "scheduler", "dashboard", "exporter",
               "ingest queue", "search index", "auth service", "backup job")
_TOPICS = ("error budget", "release checklist", "on-call rota", "load test plan",
           "cache warmup", "retry policy", "log format", "alert thresholds",
           "schema review", "dependency audit")
_TOOLS = ("Bash", "Read", "Edit", "Write", "Grep", "Glob", "WebFetch", "recall", "remember")
_BRANCHES = ("fix", "feat", "chore", "perf", "refactor", "docs", "test")
_STATUS = ("succeeded", "pending", "refunded", "failed", "requires_action")
_LEVEL = ("INFO", "WARN", "ERROR", "DEBUG")
_PROJECTS = ("core", "hub", "mesh", "loops", "site", "infra")


def _date(s: _Stream) -> str:
    return f"2026-{1 + s.below(9):02d}-{1 + s.below(28):02d}"


def _receipt(s: _Stream, i: int) -> str:
    return (f"Receipt {i}: app id asdk_app_{s.hex(16)}, customer cus_{s.alnum(14)}, "
            f"amount {s.below(500)}.{s.below(100):02d} USD, status {s.pick(_STATUS)}, "
            f"idempotency key {s.uuid()}.")


def _log(s: _Stream, i: int) -> str:
    return (f"{_date(s)}T{s.below(24):02d}:{s.below(60):02d}:{s.below(60):02d}Z "
            f"{s.pick(_LEVEL)} worker pid={1000 + s.below(60000)} request_id=req_{s.alnum(20)} "
            f"trace={s.hex(32)} latency_ms={s.below(4000)} "
            f"status={s.pick((200, 201, 204, 400, 404, 409, 429, 500, 502))}")


def _session(s: _Stream, i: int) -> str:
    return (f"Session {s.uuid()} ended. {s.below(40)} memories stored, {s.below(25)} recalls, "
            f"duration {1 + s.below(180)} min.")


def _commit(s: _Stream, i: int) -> str:
    return (f"Commit {s.hex(7)} on branch "
            f"{s.pick(_BRANCHES)}/{s.pick(_COMPONENTS).replace(' ', '-')}-{i}: "
            f"{s.pick(('adjust', 'rename', 'guard', 'split', 'inline'))} {s.pick(_COMPONENTS)} "
            f"({s.hex(40)}).")


def _tool_event(s: _Stream, i: int) -> str:
    return (f"tool_event id={s.uuid()} tool={s.pick(_TOOLS)} exit={s.pick((0, 0, 0, 1, 2))} "
            f"bytes={s.below(200000)} sha256={s.hex(64)}")


def _meeting(s: _Stream, i: int) -> str:
    return (f"Meeting {i}: {s.pick(_PEOPLE)} said the {s.pick(_COMPONENTS)} "
            f"{s.pick(_TOPICS)} should be "
            f"revisited in sprint {10 + s.below(40)}; {s.pick(_PEOPLE)} will draft the notes.")


def _maintenance(s: _Stream, i: int) -> str:
    # Mirrors the shape of an earlier synthetic audit's distractor rows.
    component = s.pick(_COMPONENTS)
    return (f"On {_date(s)}, {s.pick(_PROJECTS).title()} {component} maintenance ticket "
            f"AUD-{1000 + i} was reviewed by {s.pick(_PEOPLE)}. We recorded {20 + s.below(37)} "
            f"successful checks and {s.below(4)} retries. The {component} build is "
            f"v{1 + s.below(4)}.{s.below(12)}.{s.below(30)}. "
            f"Next review is in {1 + s.below(14)} days.")


def _stub(s: _Stream, i: int) -> str:
    # Under 40 characters, as real stubs are.
    form = i % 5
    if form == 0:
        return f"[] session ended {_date(s)} #{i}"
    if form == 1:
        return f"rcpt asdk_app_{s.hex(12)} ok"
    if form == 2:
        return f"ok {s.hex(8)} ({i})"
    if form == 3:
        return f"run {s.uuid()[:23]}"
    return f"saved {s.below(9)} item(s) #{i}"


_MAKERS = {
    "receipt": _receipt, "log": _log, "session": _session, "commit": _commit,
    "tool_event": _tool_event, "meeting": _meeting, "maintenance": _maintenance,
    "stub": _stub,
}


def _slice_of(key: str) -> str:
    h = int.from_bytes(hashlib.sha256(f"{CORPUS_VERSION}:slice:{key}".encode()).digest()[:4], "big")
    return LEGACY if h % 10 < _LEGACY_TENTHS else DESCRIBED


def _lifecycle_of(key: str, *, anchor: bool) -> str:
    h = int.from_bytes(hashlib.sha256(f"{CORPUS_VERSION}:life:{key}".encode()).digest()[:4], "big")
    r = h % 100
    if anchor:  # a question never points at an archived memory
        return "active" if r < 30 else "warm" if r < 85 else "cold"
    return "active" if r < 26 else "warm" if r < 84 else "cold" if r < 98 else "archived"


def anchors() -> list[Memory]:
    rows = []
    stream = _Stream(CORPUS_VERSION + ":anchor-dates")
    for line in (FIXTURES / "anchors.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows.append(Memory(row["key"], row["content"], row["kind"], row["project"],
                               _date(stream), _slice_of(row["key"]),
                               _lifecycle_of(row["key"], anchor=True)))
    return rows


def fillers() -> list[Memory]:
    rows: list[Memory] = []
    for kind, count in _FILLER_COUNTS.items():
        stream = _Stream(f"{CORPUS_VERSION}:{kind}")
        for i in range(count):
            content = _MAKERS[kind](stream, i)
            key = f"fill-{kind}-{i}"
            rows.append(Memory(key, content, kind, _PROJECTS[i % len(_PROJECTS)],
                               _date(stream), _slice_of(key), _lifecycle_of(key, anchor=False)))
    return rows


def interleave(first: list[Memory], second: list[Memory]) -> list[Memory]:
    """Spread the anchors through the filler, as real writes would be."""
    if not first:
        return list(second)
    step = max(1, len(second) // len(first))
    out: list[Memory] = []
    pending = list(first)
    for i, row in enumerate(second):
        if pending and i % step == 0:
            out.append(pending.pop(0))
        out.append(row)
    return out + pending


def corpus() -> list[Memory]:
    rows = interleave(anchors(), fillers())
    seen = set()
    for row in rows:
        if row.key in seen or row.content in seen:
            raise ValueError(f"duplicate row in the answerability corpus: {row.key}")
        seen.add(row.key)
        seen.add(row.content)
    return rows


def load_questions(name: str) -> list[dict]:
    path = FIXTURES / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def digest() -> str:
    """Identifies the store: corpus rows plus the generator version."""
    h = hashlib.sha256(CORPUS_VERSION.encode("utf-8"))
    for row in corpus():
        h.update(json.dumps([row.key, row.content, row.kind, row.project, row.session_date,
                             row.slice, row.lifecycle], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


def slice_of_key() -> dict[str, str]:
    return {row.key: row.slice for row in corpus()}
