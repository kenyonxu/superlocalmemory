# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Settle a bandit play from an authenticated outcome.

WHY THIS FILE EXISTS
--------------------
``reward_proxy.py`` has said since v3.4.22:

    Replaced in v3.4.22 by ``reward_from_outcomes.py`` — DO NOT extend this
    module beyond the proxy window contract.

That file was never written. So the replacement never arrived, and the module
that tells you not to extend it remained the only settlement path — one that
cannot work. Measured, not inferred:

* ``reward_proxy._tool_event_hit`` selects ``payload_json`` from ``tool_events``
  ``WHERE occurred_at BETWEEN ? AND ?``. **Neither column exists** on that
  table, on this install or in any DDL in the codebase. The query raises
  ``no such column: payload_json``, a bare ``except sqlite3.Error`` returns
  False, and the "cited" branch of the ladder is therefore unreachable.
* Even with the names corrected there would be nothing to find: **0 of 2,002**
  ``tool_events`` rows on a live store contain a 16-hex token, let alone a
  real ``fact_id``. That table records tool names and summaries, not the
  memories a recall returned.

So every settlement fell through to the 120-second default of 0.5, which is
visible in the arms as an exact tie::

    SUM(alpha) = SUM(beta) = 867.5 = 165 priors + 1,405 plays x 0.5

WHAT THIS USES INSTEAD
----------------------
``action_outcomes`` (memory.db, M006) — the table an *explicit* outcome report
already writes, with a reward the reporter chose:
``success=1.0 / failure=0.0 / partial=0.5``. That is an authenticated signal
about whether the memories helped, which is exactly what a play needs and what
an exposure is not.

It carries ``recall_query_id``, a join key straight to ``bandit_plays.query_id``.
**0 of 162 rows populate it** — the column was added and never written. This
module reads it when present, and otherwise falls back to overlap between the
outcome's ``fact_ids_json`` and the ``shown_fact_ids`` the play recorded (M044).

The fallback is the load-bearing path, not a nicety: recall does not return its
``query_id`` to the caller (``server/recall_serializer.py`` has no such field),
so no existing client *can* supply one. Overlap on the memories actually shown,
inside a time window, is the strongest link available without changing every
caller first.

THE GRACE WINDOW, AND WHY IT IS NOT 120 SECONDS
-----------------------------------------------
``reward_proxy`` defaults a play to 0.5 once it is 120 seconds old. A human or
an agent deciding whether a memory helped does not do so within two minutes, so
that deadline would claim every play before any real outcome arrived — the
default would win a race it should not be in.

A play that recorded ``shown_fact_ids`` therefore waits ``_GRACE_SEC`` (default
900) before it may be defaulted. A play with no recorded facts keeps the old
120-second behaviour, because nothing can ever settle it from evidence and
holding it open buys nothing.

NOTHING HERE FABRICATES A REWARD. If no outcome is reported, the play settles
neutral and is *labelled* ``default`` so it stays distinguishable from a
genuinely neutral outcome. Those two were indistinguishable before, which is why
this went unnoticed for months.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from superlocalmemory.learning.bandit import ContextualBandit
from superlocalmemory.learning.pcos import update_scores

logger = logging.getLogger(__name__)

__all__ = ["settle_from_outcomes", "GRACE_SEC", "SETTLEMENT_KIND"]

#: How long a play that recorded its shown memories waits for a reported
#: outcome before it may be defaulted. Deliberately far longer than the proxy's
#: 120 s: see the module docstring.
GRACE_SEC: float = float(os.environ.get("SLM_OUTCOME_GRACE_SEC", "900"))

#: How far after a play an outcome may arrive and still be attributed to it.
#: Same order as the grace window — an outcome reported later than this is about
#: something else.
_MATCH_WINDOW_SEC: float = GRACE_SEC

#: Written to ``bandit_plays.settlement_type``. A settlement that came from a
#: real report must never be mistaken for the neutral default.
SETTLEMENT_KIND = "outcome_reported"

_MAX_PLAYS_PER_PASS = 500


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _open(path: Path) -> sqlite3.Connection | None:
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        logger.debug("reward_from_outcomes: open %s failed: %s", path, exc)
        return None


def _shown(raw: object) -> set[str]:
    """``shown_fact_ids`` JSON -> set. Empty on anything unexpected."""
    if not raw:
        return set()
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(f) for f in parsed if f}


def _unsettled_plays(
    conn: sqlite3.Connection, profile_id: str,
) -> list[sqlite3.Row]:
    """Unsettled plays, oldest first. Degrades if M044 has not run."""
    for columns in (
        "play_id, query_id, played_at, shown_fact_ids",
        "play_id, query_id, played_at, NULL AS shown_fact_ids",
    ):
        try:
            return conn.execute(
                f"SELECT {columns} FROM bandit_plays "
                "WHERE profile_id = ? AND settled_at IS NULL "
                "ORDER BY played_at ASC LIMIT ?",
                (str(profile_id), _MAX_PLAYS_PER_PASS),
            ).fetchall()
        except sqlite3.Error as exc:
            logger.debug("reward_from_outcomes: play fetch (%s): %s", columns, exc)
    return []


def _candidate_outcomes(
    conn: sqlite3.Connection, profile_id: str, since: datetime,
) -> list[dict]:
    """Reported outcomes from ``since`` onward, newest first.

    Read once per pass rather than once per play: the window holds a handful of
    rows in practice, and a per-play query would scan this table N times on the
    settlement thread.
    """
    try:
        rows = conn.execute(
            "SELECT outcome_id, fact_ids_json, outcome, reward, timestamp, "
            "       recall_query_id "
            "FROM action_outcomes "
            "WHERE profile_id = ? AND timestamp >= ? "
            "ORDER BY timestamp DESC LIMIT 2000",
            (str(profile_id), since.isoformat()),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug("reward_from_outcomes: outcome fetch: %s", exc)
        return []

    out: list[dict] = []
    for r in rows:
        at = _parse_iso(r["timestamp"])
        if at is None:
            continue
        try:
            facts = json.loads(r["fact_ids_json"] or "[]")
        except (TypeError, ValueError):
            facts = []
        if not isinstance(facts, list):
            facts = []
        try:
            reward = float(r["reward"])
        except (TypeError, ValueError):
            continue
        out.append({
            "id": str(r["outcome_id"]),
            "at": at,
            "facts": {str(f) for f in facts if f},
            "reward": max(0.0, min(1.0, reward)),
            "query_id": str(r["recall_query_id"] or ""),
        })
    return out


def _match(
    play_at: datetime,
    query_id: str,
    shown: set[str],
    outcomes: list[dict],
    claimed: set[str],
) -> dict | None:
    """The one outcome attributable to this play, or None.

    Returns the outcome itself rather than its reward, because the caller needs
    to know WHICH memories that outcome named — see ``settle_from_outcomes``.

    AN OUTCOME IS EVIDENCE ABOUT ONE RECALL, AND IS CONSUMED
    -------------------------------------------------------
    The first version of this matched an outcome against every unsettled play
    whose shown memories intersected it, and never consumed it. Both audits
    reproduced the consequence and it is severe: five recalls that happened to
    display the same memory, plus one reported outcome, settled **five** plays
    and moved five arms; twenty recalls settled twenty. One person saying "that
    helped" became twenty pieces of evidence about twenty different retrieval
    strategies, most of which had nothing to do with it.

    It also inflated confidence. ``play_count`` is what
    ``pcos.confidence_weight`` reads, so the same fan-out could push a memory to
    "proven" — full bonus — on a single judgement.

    The effect on the learning claim is the part that matters: Thompson sampling
    would be learning which memories were *nearby*, not which strategy helped.
    That is how arms come off their prior and the ranker is still random.

    So ``claimed`` carries the outcome ids already spent this pass, and an
    outcome may be spent once. Plays are offered outcomes oldest-first, so the
    recall that ran first gets the credit — the report is far more likely to be
    about the answer the user actually saw.

    THE EXACT PATH IS ALSO BOUNDED IN TIME. A ``recall_query_id`` is a stronger
    statement of intent than overlap and is tried first, but it used to skip the
    horizon check entirely: an outcome ten days later, naming completely
    different memories, settled a long-closed play at reward 0.0. Reproduced.
    Both paths now require the outcome to fall inside the window.
    """
    horizon = play_at + timedelta(seconds=_MATCH_WINDOW_SEC)

    def _in_window(o: dict) -> bool:
        return play_at <= o["at"] <= horizon

    def _first(candidates: list[dict]) -> dict | None:
        fresh = [o for o in candidates if o["id"] not in claimed]
        return min(fresh, key=lambda o: o["at"]) if fresh else None

    if query_id:
        exact = _first([
            o for o in outcomes
            if o["query_id"] and o["query_id"] == query_id and _in_window(o)
        ])
        if exact is not None:
            return exact

    if not shown:
        return None
    return _first([
        o for o in outcomes if _in_window(o) and (o["facts"] & shown)
    ])


def settle_from_outcomes(
    profile_id: str,
    learning_db: Path | str,
    memory_db: Path | str,
    *,
    now: datetime | None = None,
    bandit: ContextualBandit | None = None,
) -> int:
    """Settle plays from reported outcomes. Returns the count. Never raises.

    Run this BEFORE ``reward_proxy.settle_stale_plays`` on any pass. The proxy
    defaults a play at 120 seconds; if it goes first it takes every play with
    it and a reported outcome arriving at minute five finds nothing left to
    settle.
    """
    current = now or datetime.now(timezone.utc)
    learning_conn = _open(Path(learning_db))
    if learning_conn is None:
        return 0
    memory_conn = _open(Path(memory_db))
    if memory_conn is None:
        learning_conn.close()
        return 0

    owns_bandit = bandit is None
    if bandit is None:
        bandit = ContextualBandit(Path(learning_db), profile_id=str(profile_id))

    settled = 0
    try:
        plays = _unsettled_plays(learning_conn, profile_id)
        if not plays:
            return 0
        oldest = min(
            (p for p in (_parse_iso(r["played_at"]) for r in plays)
             if p is not None),
            default=current,
        )
        outcomes = _candidate_outcomes(memory_conn, profile_id, oldest)
        if not outcomes:
            return 0

        # Outcome ids spent this pass. An outcome is evidence about one recall.
        claimed: set[str] = set()

        for row in plays:
            played = _parse_iso(row["played_at"])
            if played is None:
                continue
            shown = _shown(row["shown_fact_ids"])
            outcome = _match(
                played, str(row["query_id"] or ""), shown, outcomes, claimed,
            )
            if outcome is None:
                continue
            if bandit.update(int(row["play_id"]), outcome["reward"],
                             kind=SETTLEMENT_KIND):
                settled += 1
                claimed.add(outcome["id"])
                # Credit ONLY the memories the outcome actually named. Writing
                # it to everything the play displayed spread one judgement
                # across up to five memories: an outcome naming one of them
                # moved all five to 0.55 with play_count 1. Co-displayed
                # memories inherited credit, the bonus then lifted them, and
                # they were shown again — the rich-get-richer path this was
                # built to prevent, one hop removed.
                judged = sorted(outcome["facts"] & shown) if shown else []
                if judged:
                    try:
                        update_scores(
                            memory_conn, profile_id, judged, outcome["reward"],
                        )
                        memory_conn.commit()
                    except Exception as exc:  # pragma: no cover — defensive
                        logger.debug("pcos update skipped: %s", exc)
    except sqlite3.Error as exc:  # pragma: no cover — defensive
        logger.warning("reward_from_outcomes: %s", exc)
    finally:
        for conn in (learning_conn, memory_conn):
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass
        if owns_bandit:
            try:
                from superlocalmemory.learning.bandit import (
                    close_threadlocal_conn,
                )

                close_threadlocal_conn()
            except Exception:  # pragma: no cover — defensive
                pass
    if settled:
        logger.info(
            "reward_from_outcomes: settled %d play(s) from reported outcomes",
            settled,
        )
    return settled
