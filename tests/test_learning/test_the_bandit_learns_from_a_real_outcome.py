# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""The learning loop, end to end, asserted the only way that proves anything.

``alpha != beta`` after settlement. Nothing else is evidence.

WHAT WAS WRONG, MEASURED
------------------------
On a live store, 165 of 165 arms had ``alpha == beta``, and the sums gave
the reason exactly::

    SUM(alpha) = SUM(beta) = 867.5 = 165 priors x 1.0 + 1,405 plays x 0.5

Every one of 1,405 settlements applied the neutral 0.5. Thompson sampling on
Beta(a, a) is a coin flip, so retrieval strategy was chosen at random for
months. Four independent causes, each sufficient on its own:

1. **No play was recorded.** ``record_signals=False`` was hardcoded at the only
   caller on 2026-07-27 (``cbf7929f``, release 3.8.6) — the same day
   ``MAX(bandit_arms.last_played_at)`` stops. It made recall take
   ``choose_readonly()``, which returns ``play_id=None`` and writes nothing. One
   flag gated both the play (no reward, one INSERT) and a twenty-row exposure
   enqueue; only the second was ever meant to be off.
2. **The evidence query named columns that do not exist.**
   ``reward_proxy._tool_event_hit`` selects ``payload_json`` from
   ``tool_events`` ``WHERE occurred_at BETWEEN ? AND ?``. Neither column is on
   that table, anywhere in the codebase. It raises, a bare
   ``except sqlite3.Error`` returns False, and the "cited" branch is
   unreachable.
3. **There was nothing to find there anyway** — 0 of 2,002 ``tool_events`` rows
   contain a fact_id.
4. **The join key was never written.** ``action_outcomes.recall_query_id`` has
   existed since M006; 0 of 162 rows populate it.

And ``reward_proxy``'s own docstring points at a replacement,
``reward_from_outcomes.py``, that had never been written.

Every test below fails against the code as it shipped in 4.0.10.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from superlocalmemory.learning.bandit import ContextualBandit
from superlocalmemory.learning.reward_from_outcomes import (
    SETTLEMENT_KIND,
    settle_from_outcomes,
)
from superlocalmemory.storage.migrations import M005_bandit_tables as _M005
from superlocalmemory.storage.migrations import (
    M044_play_carries_its_own_evidence as _M044,
)
from superlocalmemory.storage.migrations import (
    M045_fact_outcome_score as _M045,
)

_PROFILE = "default"
_FACT_SHOWN = "aaaaaaaaaaaaaaaa"
_FACT_OTHER = "bbbbbbbbbbbbbbbb"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture()
def store(tmp_path: Path) -> tuple[Path, Path]:
    """A learning.db with the bandit tables + M044, and a memory.db."""
    learning = tmp_path / "learning.db"
    conn = sqlite3.connect(str(learning))
    conn.executescript(_M005.DDL)
    _M044.apply(conn)
    conn.commit()
    conn.close()

    memory = tmp_path / "memory.db"
    conn = sqlite3.connect(str(memory))
    conn.execute(
        "CREATE TABLE action_outcomes ("
        " outcome_id TEXT PRIMARY KEY, profile_id TEXT, query TEXT,"
        " fact_ids_json TEXT, outcome TEXT, context_json TEXT,"
        " timestamp TEXT, reward REAL, settled INTEGER, settled_at TEXT,"
        " recall_query_id TEXT)"
    )
    # Settlement also folds the reward into the per-fact score, so the table
    # M045 creates has to be here or the PCOS write is silently skipped and a
    # credit-attribution test proves nothing.
    _M045.apply(conn)
    conn.commit()
    conn.close()
    return learning, memory


def _report(
    memory: Path, *, facts: list[str], reward: float, at: datetime,
    query_id: str = "", outcome: str = "success",
) -> None:
    conn = sqlite3.connect(str(memory))
    conn.execute(
        "INSERT INTO action_outcomes (outcome_id, profile_id, query,"
        " fact_ids_json, outcome, context_json, timestamp, reward, settled,"
        " settled_at, recall_query_id) VALUES (?,?,'',?,?,'{}',?,?,1,?,?)",
        (f"o-{at.timestamp()}-{outcome}", _PROFILE, json.dumps(facts),
         outcome, _iso(at), reward, _iso(at), query_id),
    )
    conn.commit()
    conn.close()


def _arms(learning: Path) -> list[tuple[str, float, float, int]]:
    conn = sqlite3.connect(str(learning))
    rows = conn.execute(
        "SELECT arm_id, alpha, beta, plays FROM bandit_arms"
    ).fetchall()
    conn.close()
    return rows


def _play_row(learning: Path, play_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(str(learning))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM bandit_plays WHERE play_id = ?", (play_id,)
    ).fetchone()
    conn.close()
    return row


def _drive_a_recall(learning: Path, query_id: str = "q-1") -> int:
    """Record a play and tell it which memory it showed."""
    bandit = ContextualBandit(learning, profile_id=_PROFILE)
    choice = bandit.choose({"query_type": "open_domain", "entity_count": 0},
                           query_id)
    assert choice.play_id, "choose() did not record a play"
    assert bandit.record_shown(choice.play_id, [_FACT_SHOWN, _FACT_OTHER])
    return choice.play_id


class TestTheLoopCloses:
    def test_a_reported_success_moves_alpha_and_not_beta(self, store) -> None:
        """The one assertion that proves it. Everything else is plumbing."""
        learning, memory = store
        play_id = _drive_a_recall(learning)
        played = _play_row(learning, play_id)["played_at"]
        at = datetime.fromisoformat(played) + timedelta(seconds=90)

        _report(memory, facts=[_FACT_SHOWN], reward=1.0, at=at)
        n = settle_from_outcomes(_PROFILE, learning, memory,
                                 now=at + timedelta(seconds=30))
        assert n == 1, "no play settled from a reported outcome"

        arms = _arms(learning)
        assert len(arms) == 1
        arm_id, alpha, beta, plays = arms[0]
        assert alpha != beta, (
            f"arm {arm_id} still has alpha == beta ({alpha}) — the loop is "
            "recording plays but learning nothing, which is the original bug"
        )
        assert alpha == pytest.approx(2.0), alpha   # prior 1.0 + reward 1.0
        assert beta == pytest.approx(1.0), beta     # prior 1.0 + 0.0
        assert plays == 1

    def test_a_reported_failure_moves_beta_the_other_way(self, store) -> None:
        learning, memory = store
        play_id = _drive_a_recall(learning)
        at = datetime.fromisoformat(
            _play_row(learning, play_id)["played_at"]
        ) + timedelta(seconds=90)
        _report(memory, facts=[_FACT_SHOWN], reward=0.0, at=at,
                outcome="failure")
        assert settle_from_outcomes(_PROFILE, learning, memory,
                                    now=at + timedelta(seconds=30)) == 1
        _arm, alpha, beta, _plays = _arms(learning)[0]
        assert beta > alpha, (alpha, beta)

    def test_the_settlement_is_labelled_as_a_real_outcome(self, store) -> None:
        """A default and a genuine neutral must never look alike.

        They were indistinguishable before, which is why 1,405 consecutive
        neutral settlements went unnoticed for months.
        """
        learning, memory = store
        play_id = _drive_a_recall(learning)
        at = datetime.fromisoformat(
            _play_row(learning, play_id)["played_at"]
        ) + timedelta(seconds=90)
        _report(memory, facts=[_FACT_SHOWN], reward=0.5, at=at,
                outcome="partial")
        settle_from_outcomes(_PROFILE, learning, memory,
                             now=at + timedelta(seconds=30))
        row = _play_row(learning, play_id)
        assert row["settlement_type"] == SETTLEMENT_KIND
        assert row["settlement_type"] != "default"
        assert row["reward"] == pytest.approx(0.5)


class TestWhatIsMatchedToWhat:
    def test_an_explicit_recall_query_id_wins(self, store) -> None:
        learning, memory = store
        play_id = _drive_a_recall(learning, query_id="q-exact")
        at = datetime.fromisoformat(
            _play_row(learning, play_id)["played_at"]
        ) + timedelta(seconds=30)
        # No fact overlap at all — only the query id ties them together.
        _report(memory, facts=["cccccccccccccccc"], reward=1.0, at=at,
                query_id="q-exact")
        assert settle_from_outcomes(_PROFILE, learning, memory,
                                    now=at + timedelta(seconds=60)) == 1

    def test_an_outcome_about_other_memories_settles_nothing(
        self, store,
    ) -> None:
        """Attribution must be earned. Otherwise every arm learns from noise."""
        learning, memory = store
        play_id = _drive_a_recall(learning)
        at = datetime.fromisoformat(
            _play_row(learning, play_id)["played_at"]
        ) + timedelta(seconds=60)
        _report(memory, facts=["cccccccccccccccc"], reward=1.0, at=at)
        assert settle_from_outcomes(_PROFILE, learning, memory,
                                    now=at + timedelta(seconds=60)) == 0
        _arm, alpha, beta, _p = _arms(learning)[0] if _arms(learning) else (
            "", 1.0, 1.0, 0)
        assert alpha == beta

    def test_an_outcome_reported_before_the_recall_is_not_evidence(
        self, store,
    ) -> None:
        """A judgement cannot precede the answer it judges."""
        learning, memory = store
        play_id = _drive_a_recall(learning)
        played = datetime.fromisoformat(_play_row(learning, play_id)["played_at"])
        _report(memory, facts=[_FACT_SHOWN], reward=1.0,
                at=played - timedelta(seconds=120))
        assert settle_from_outcomes(
            _PROFILE, learning, memory, now=played + timedelta(seconds=300),
        ) == 0

    def test_an_outcome_long_after_the_recall_is_not_evidence(
        self, store,
    ) -> None:
        learning, memory = store
        play_id = _drive_a_recall(learning)
        played = datetime.fromisoformat(_play_row(learning, play_id)["played_at"])
        from superlocalmemory.learning.reward_from_outcomes import GRACE_SEC

        late = played + timedelta(seconds=GRACE_SEC + 600)
        _report(memory, facts=[_FACT_SHOWN], reward=1.0, at=late)
        assert settle_from_outcomes(
            _PROFILE, learning, memory, now=late + timedelta(seconds=60),
        ) == 0

    def test_a_play_settles_only_once(self, store) -> None:
        learning, memory = store
        play_id = _drive_a_recall(learning)
        at = datetime.fromisoformat(
            _play_row(learning, play_id)["played_at"]
        ) + timedelta(seconds=60)
        _report(memory, facts=[_FACT_SHOWN], reward=1.0, at=at)
        now = at + timedelta(seconds=60)
        assert settle_from_outcomes(_PROFILE, learning, memory, now=now) == 1
        assert settle_from_outcomes(_PROFILE, learning, memory, now=now) == 0
        _arm, alpha, _beta, plays = _arms(learning)[0]
        assert plays == 1, "the same outcome was applied twice"


class TestTheDefaultDoesNotWinTheRace:
    def test_a_play_with_evidence_is_not_defaulted_at_120_seconds(
        self, store,
    ) -> None:
        """The bug this guards is subtle and total.

        ``reward_proxy`` defaults a play to 0.5 at 120 s. Nobody decides whether
        a memory helped inside two minutes, so the default would claim every
        play before a real outcome arrived — and the loop would look like it was
        running while learning nothing, exactly as it has since July.
        """
        from superlocalmemory.learning.reward_proxy import settle_stale_plays

        learning, memory = store
        play_id = _drive_a_recall(learning)
        played = datetime.fromisoformat(_play_row(learning, play_id)["played_at"])

        # Three minutes in: past the 120 s window, inside the grace period.
        assert settle_stale_plays(
            _PROFILE, learning, memory, now=played + timedelta(seconds=180),
        ) == 0, "the neutral default claimed a play that could still be settled"
        assert _play_row(learning, play_id)["settled_at"] is None

    def test_a_play_with_no_evidence_still_defaults_at_120_seconds(
        self, store,
    ) -> None:
        """Holding open a play nothing can settle just leaks rows."""
        from superlocalmemory.learning.reward_proxy import settle_stale_plays

        learning, memory = store
        bandit = ContextualBandit(learning, profile_id=_PROFILE)
        choice = bandit.choose({"query_type": "open_domain",
                                "entity_count": 0}, "q-bare")
        # deliberately no record_shown
        played = datetime.fromisoformat(
            _play_row(learning, choice.play_id)["played_at"]
        )
        assert settle_stale_plays(
            _PROFILE, learning, memory, now=played + timedelta(seconds=180),
        ) == 1
        row = _play_row(learning, choice.play_id)
        assert row["settlement_type"] == "default"
        assert row["reward"] == pytest.approx(0.5)


class TestItDegradesRatherThanFailing:
    def test_settlement_works_on_a_store_without_m044(
        self, tmp_path: Path,
    ) -> None:
        """An install mid-upgrade must not lose settlement entirely."""
        learning = tmp_path / "learning.db"
        conn = sqlite3.connect(str(learning))
        conn.executescript(_M005.DDL)          # no M044
        conn.commit()
        conn.close()
        memory = tmp_path / "memory.db"
        conn = sqlite3.connect(str(memory))
        conn.execute(
            "CREATE TABLE action_outcomes (outcome_id TEXT PRIMARY KEY,"
            " profile_id TEXT, query TEXT, fact_ids_json TEXT, outcome TEXT,"
            " context_json TEXT, timestamp TEXT, reward REAL,"
            " settled INTEGER, settled_at TEXT, recall_query_id TEXT)"
        )
        conn.commit()
        conn.close()

        bandit = ContextualBandit(learning, profile_id=_PROFILE)
        choice = bandit.choose({"query_type": "open_domain",
                                "entity_count": 0}, "q-nom044")
        assert choice.play_id
        assert bandit.record_shown(choice.play_id, [_FACT_SHOWN]) is False, (
            "record_shown must report failure, not raise, without the column"
        )
        at = datetime.now(timezone.utc)
        _report(memory, facts=[_FACT_SHOWN], reward=1.0, at=at,
                query_id="q-nom044")
        # The exact query-id path still works with no shown_fact_ids column.
        assert settle_from_outcomes(
            _PROFILE, learning, memory, now=at + timedelta(seconds=30),
        ) == 1

    def test_a_missing_outcomes_table_is_not_an_exception(
        self, tmp_path: Path,
    ) -> None:
        learning = tmp_path / "learning.db"
        conn = sqlite3.connect(str(learning))
        conn.executescript(_M005.DDL)
        _M044.apply(conn)
        conn.commit()
        conn.close()
        _drive_a_recall(learning)
        empty = tmp_path / "empty.db"
        sqlite3.connect(str(empty)).close()
        assert settle_from_outcomes(_PROFILE, learning, empty) == 0


class TestTheReplacementModuleActuallyExists:
    def test_reward_proxy_no_longer_points_at_a_missing_file(self) -> None:
        """Its docstring named this module for months. Nobody wrote it.

        Keeping this assertion cheap and blunt: an import that resolves is the
        whole claim.
        """
        import importlib

        mod = importlib.import_module(
            "superlocalmemory.learning.reward_from_outcomes"
        )
        assert callable(mod.settle_from_outcomes)

    def test_the_loop_settles_outcomes_before_it_defaults(self) -> None:
        """Order is the whole design. Reversed, the default wins every race.

        Asserted from the AST. The first version compared
        ``src.index("settle_from_outcomes,")`` against
        ``src.index("settle_stale_plays,")`` — and the first match for the
        former is the IMPORT at offset 264 while the call sits at 1009, so the
        assertion passed no matter which order the calls were in. An audit
        demonstrated it by swapping them. A substring offset cannot answer "which
        is called first"; the tree can.
        """
        import ast
        import inspect
        import textwrap

        from superlocalmemory.server import bandit_loops

        tree = ast.parse(textwrap.dedent(
            inspect.getsource(bandit_loops._reward_proxy_loop)
        ))
        # to_thread(fn, ...) — the settler is the FIRST ARGUMENT, so read that
        # rather than the name of the call being made.
        dispatched: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(
                node.func, "attr", None
            ) == "to_thread" and node.args:
                target = getattr(node.args[0], "id", None) or getattr(
                    node.args[0], "attr", None
                )
                if target:
                    dispatched.append((node.lineno, target))
        order = [n for _, n in sorted(dispatched)]
        assert "settle_from_outcomes" in order, (
            "the outcome settler is never dispatched, so a reported outcome "
            f"never reaches an arm: {order}"
        )
        assert "settle_stale_plays" in order, order
        assert order.index("settle_from_outcomes") < order.index(
            "settle_stale_plays"
        ), (
            "the neutral default sweep runs before the outcome settler, so it "
            f"claims every play first: {order}"
        )


class TestOneOutcomeIsEvidenceAboutOneRecall:
    """The tests above never constructed two plays. That was the whole gap.

    The consequence was reproduced: an outcome was matched against
    every unsettled play whose shown memories intersected it, and never
    consumed. Five recalls displaying the same memory plus one reported outcome
    settled five plays and moved five arms. Twenty settled twenty.

    The damage is not just arithmetic. The arm is the retrieval STRATEGY of one
    query, so crediting twenty arms for one judgement teaches the sampler which
    memories were nearby rather than which strategy helped — arms come off their
    prior and the ranker stays random, which is indistinguishable from success
    by every check that looks only at ``alpha != beta``.
    """

    @staticmethod
    def _play(learning: Path, qid: str, shown: list[str]) -> int:
        bandit = ContextualBandit(learning, profile_id=_PROFILE)
        choice = bandit.choose(
            {"query_type": "open_domain", "entity_count": 0}, qid,
        )
        assert choice.play_id
        bandit.record_shown(choice.play_id, shown)
        return choice.play_id

    @staticmethod
    def _moved(learning: Path) -> int:
        conn = sqlite3.connect(str(learning))
        n = conn.execute(
            "SELECT COUNT(*) FROM bandit_arms WHERE alpha != beta"
        ).fetchone()[0]
        conn.close()
        return n

    def test_one_outcome_settles_one_play_not_every_overlapping_one(
        self, store,
    ) -> None:
        learning, memory = store
        for i in range(5):
            self._play(learning, f"q-{i}", ["fact-shared"])
        played = datetime.fromisoformat(
            _play_row(learning, 1)["played_at"]
        )
        _report(memory, facts=["fact-shared"], reward=1.0,
                at=played + timedelta(seconds=30))
        n = settle_from_outcomes(
            _PROFILE, learning, memory, now=played + timedelta(seconds=120),
        )
        assert n == 1, (
            f"one reported outcome settled {n} plays; it is evidence about the "
            "recall it was reported against, not about every recall that "
            "happened to display the same memory"
        )

    def test_the_credited_memory_is_the_one_that_was_judged(
        self, store,
    ) -> None:
        """Settlement used to write the reward to everything displayed.

        An outcome naming one of five shown memories moved all five to 0.55
        with play_count 1 — and play_count is what the confidence weight reads,
        so co-displayed memories were promoted toward "proven" on a judgement
        that was never about them.
        """
        learning, memory = store
        self._play(learning, "q-1", ["JUDGED", "co-1", "co-2", "co-3", "co-4"])
        played = datetime.fromisoformat(_play_row(learning, 1)["played_at"])
        _report(memory, facts=["JUDGED"], reward=1.0,
                at=played + timedelta(seconds=30))
        settle_from_outcomes(
            _PROFILE, learning, memory, now=played + timedelta(seconds=120),
        )
        conn = sqlite3.connect(str(memory))
        scored = [r[0] for r in conn.execute(
            "SELECT fact_id FROM fact_outcome_score ORDER BY fact_id"
        )]
        conn.close()
        assert scored == ["JUDGED"], (
            f"credit reached memories nobody judged: {scored}"
        )

    def test_two_outcomes_settle_two_plays(self, store) -> None:
        """The other direction. Consumption must not starve real evidence."""
        learning, memory = store
        for i in range(2):
            self._play(learning, f"q-{i}", ["fact-shared"])
        played = datetime.fromisoformat(_play_row(learning, 1)["played_at"])
        _report(memory, facts=["fact-shared"], reward=1.0,
                at=played + timedelta(seconds=30))
        _report(memory, facts=["fact-shared"], reward=0.0,
                at=played + timedelta(seconds=40), outcome="failure")
        n = settle_from_outcomes(
            _PROFILE, learning, memory, now=played + timedelta(seconds=120),
        )
        assert n == 2, f"two outcomes should settle two plays, settled {n}"

    def test_an_exact_query_id_still_has_to_be_in_the_window(
        self, store,
    ) -> None:
        """A judgement cannot arrive ten days late and still be about this.

        The exact-id branch skipped the horizon entirely: an outcome ten days
        later, naming completely different memories, settled a long-closed play
        at reward 0.0. Reproduced before the fix.
        """
        learning, memory = store
        self._play(learning, "q-reused", ["fact-A"])
        played = datetime.fromisoformat(_play_row(learning, 1)["played_at"])
        _report(memory, facts=["something-else"], reward=0.0,
                at=played + timedelta(days=10), query_id="q-reused",
                outcome="failure")
        n = settle_from_outcomes(
            _PROFILE, learning, memory,
            now=played + timedelta(days=10, seconds=60),
        )
        assert n == 0, "an outcome ten days later settled the play"
        assert _play_row(learning, 1)["settled_at"] is None


class TestAPlayWithNoEvidenceIsNotHeldOpen:
    def test_an_empty_shown_list_does_not_buy_the_grace_window(self) -> None:
        """``"[]"`` is truthy, which is the whole bug.

        A play that recorded no memories was given the full grace window it can
        never use — nothing can overlap an empty set — so it stayed unsettled
        forever, and the retention sweep only removes SETTLED rows. Reproduced:
        ``"[]"`` returned 900 s where it must return 120 s.
        """
        from superlocalmemory.learning.reward_proxy import (
            _MAX_AGE_SEC,
            _default_deadline,
        )

        class _Row(dict):
            def __getitem__(self, key):
                if key not in self:
                    raise KeyError(key)
                return dict.get(self, key)

        assert _default_deadline(None, _Row(shown_fact_ids="[]")) == float(
            _MAX_AGE_SEC
        ), "an empty list still buys the grace window"
        assert _default_deadline(None, _Row(shown_fact_ids=None)) == float(
            _MAX_AGE_SEC
        )
        assert _default_deadline(None, _Row(shown_fact_ids="not json")) == float(
            _MAX_AGE_SEC
        )
        # a play that DID record memories keeps the longer window
        assert _default_deadline(
            None, _Row(shown_fact_ids='["a"]'),
        ) > float(_MAX_AGE_SEC)
