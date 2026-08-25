#!/usr/bin/env python3
"""exp12 — Does the engagement loop close? A single-factor ablation.

The paper reports a learner that recorded thousands of plays and never moved a
posterior mean. Two defects were named and repaired. This experiment asks the
question that repair alone cannot answer: with the defect present, and with it
absent, and nothing else changed, does an engagement signal reach the posterior?

THE ABLATED FACTOR is the session identifier a recall is filed under, and
nothing else. Both arms run the same workload, the same seed, the same fact
corpus, the same simulated engagement, and the same production modules:

    ContextualBandit          real, chooses and records plays
    is_conversation           real, the predicate the hot path applies
    EngagementRewardModel     real, writes pending_outcomes
    extract_features / score  real, derive the reward
    settle_stale_plays        real, settles or abstains

ARM "defect"      : recalls are filed under a synthetic id (mcp:<agent>), which
                    is what a front invents when no caller supplies one. The hot
                    path drops these, so no outcome ticket exists to match.
ARM "repaired"    : recalls are filed under the conversation id the caller gave.
ARM "no-engagement": NEGATIVE CONTROL. Identical to "repaired" — real conversation
                    ids, tickets written — except the agent never acts on what it
                    was shown. A loop that reports learning here is manufacturing
                    reward, and the experiment fails.

SAFETY: operates only on temporary databases. Never opens ~/.superlocalmemory
and never touches port 8765.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superlocalmemory.core.session_identity import is_conversation  # noqa: E402
from superlocalmemory.learning.bandit import ContextualBandit  # noqa: E402
from superlocalmemory.learning.reward import EngagementRewardModel  # noqa: E402
from superlocalmemory.learning.reward_proxy import settle_stale_plays  # noqa: E402

ROUNDS = 120
PROFILE = "default"
SEED = 20260825   # Thompson sampling is stochastic; pin it so the run reproduces.
SEED_FACTS = [
    ("f-alpha",  "postgres connection pooling uses pgbouncer transaction mode"),
    ("f-beta",   "the deploy pipeline runs migrations before the health check"),
    ("f-gamma",  "retry budgets are configured per downstream not globally"),
    ("f-delta",  "the staging cluster shares its object store with production"),
]


def _mk_memory_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE pending_outcomes (
            outcome_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            session_id TEXT NOT NULL, recall_query_id TEXT NOT NULL,
            fact_ids_json TEXT NOT NULL, query_text_hash TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL,
            signals_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE tool_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            profile_id TEXT DEFAULT 'default', project_path TEXT DEFAULT '',
            tool_name TEXT NOT NULL, event_type TEXT NOT NULL DEFAULT 'invoke',
            input_summary TEXT DEFAULT '', output_summary TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0, metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL);
        CREATE TABLE atomic_facts (
            fact_id TEXT PRIMARY KEY, content TEXT, entities_json TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO atomic_facts (fact_id, content, entities_json) VALUES (?,?,'[]')",
        SEED_FACTS,
    )
    conn.commit()
    conn.close()


def _mk_learning_db(path: Path) -> None:
    """Bandit tables, verbatim from the shipped migration schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE bandit_arms (
            profile_id TEXT NOT NULL, stratum TEXT NOT NULL,
            arm_id TEXT NOT NULL, alpha REAL NOT NULL DEFAULT 1.0,
            beta REAL NOT NULL DEFAULT 1.0, plays INTEGER NOT NULL DEFAULT 0,
            last_played_at TEXT,
            PRIMARY KEY (profile_id, stratum, arm_id)) WITHOUT ROWID;
        CREATE INDEX idx_bandit_profile_strat ON bandit_arms(profile_id, stratum);
        CREATE TABLE bandit_plays (
            play_id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL,
            query_id TEXT NOT NULL, stratum TEXT NOT NULL, arm_id TEXT NOT NULL,
            played_at TEXT NOT NULL, reward REAL, settled_at TEXT,
            settlement_type TEXT, shown_fact_ids TEXT);
        CREATE INDEX idx_plays_query ON bandit_plays(query_id);
        CREATE INDEX idx_plays_unsettled ON bandit_plays(profile_id, played_at)
            WHERE settled_at IS NULL;
        CREATE INDEX idx_plays_retention ON bandit_plays(settled_at);
        """
    )
    conn.commit(); conn.close()


def run_arm(arm: str, workdir: Path) -> dict:
    """One condition. Returns the measured posterior state."""
    random.seed(SEED)          # identical draw sequence in every arm
    mem = workdir / f"memory-{arm}.db"
    learn = workdir / f"learning-{arm}.db"
    _mk_memory_db(mem)
    _mk_learning_db(learn)

    bandit = ContextualBandit(learn, profile_id=PROFILE)
    model = EngagementRewardModel(memory_db_path=str(mem))
    t0 = datetime.now(timezone.utc) - timedelta(seconds=3600)

    conn = sqlite3.connect(mem)
    recorded = 0
    for i in range(ROUNDS):
        played = t0 + timedelta(seconds=i)
        query_id = str(uuid.uuid4())
        # The ONLY difference between the two arms:
        session_id = f"mcp:agent_{i % 3}" if arm == "defect" else str(uuid.uuid4())

        ctx = {"entity_count": (i % 5), "hour": (i % 24)}   # deterministic strata
        choice = bandit.choose(ctx, query_id)
        if choice.play_id is None:
            continue
        shown = [SEED_FACTS[i % len(SEED_FACTS)][0]]
        bandit.record_shown(choice.play_id, shown)
        # Real hot-path predicate decides whether an outcome ticket exists.
        if is_conversation(session_id, PROFILE):
            oid = model.record_recall(
                profile_id=PROFILE, session_id=session_id,
                recall_query_id=query_id, fact_ids=shown,
                query_text=f"q{i}",
            )
            if not oid or oid == "disabled":
                raise RuntimeError(f"record_recall failed at round {i}: {oid!r}")
            conn.execute(
                "UPDATE pending_outcomes SET created_at_ms = ? "
                "WHERE recall_query_id = ?",
                (int(played.timestamp() * 1000), query_id),
            )
            conn.commit()
            recorded += 1
        # The agent then acts on what it was shown. Identical in the first two
        # arms; the negative control acts on something unrelated instead.
        content = dict(SEED_FACTS)[shown[0]]
        payload = ("unrelated build tooling upgrade for the frontend bundler"
                   if arm == "no-engagement" else content)
        conn.execute(
            "INSERT INTO tool_events (session_id, profile_id, tool_name, "
            "input_summary, output_summary, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, PROFILE, "Write", payload,
             "wrote config", (played + timedelta(seconds=20)).isoformat()),
        )
        conn.commit()

        # Backdate the play so settlement sees a matured evidence window.
        lc = sqlite3.connect(learn)
        lc.execute("UPDATE bandit_plays SET played_at = ? WHERE play_id = ?",
                   (played.isoformat(), choice.play_id))
        lc.commit(); lc.close()
    conn.close()

    settled = settle_stale_plays(
        PROFILE, learn, mem,
        now=datetime.now(timezone.utc), bandit=bandit,
    )

    lc = sqlite3.connect(learn)
    arms_total = lc.execute("SELECT COUNT(*) FROM bandit_arms").fetchone()[0]
    moved = lc.execute(
        "SELECT COUNT(*) FROM bandit_arms WHERE ABS(alpha - beta) > 1e-9"
    ).fetchone()[0]
    spread = lc.execute(
        "SELECT COALESCE(MAX(ABS(alpha/(alpha+beta) - 0.5)), 0.0) FROM bandit_arms"
    ).fetchone()[0]
    kinds = dict(lc.execute(
        "SELECT COALESCE(NULLIF(settlement_type,''),'(open)'), COUNT(*) "
        "FROM bandit_plays GROUP BY 1"
    ).fetchall())
    lc.close()
    return {
        "arm": arm, "rounds": ROUNDS, "outcome_tickets_written": recorded,
        "plays_settled": settled, "arms_total": arms_total,
        "arms_moved_off_prior_mean": moved,
        "max_abs_mean_shift": round(float(spread), 6),
        "settlement_types": kinds,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="slm-exp12-") as td:
        work = Path(td)
        out = {
            "experiment": "exp12_learning_loop_ablation",
            "ablated_factor": "session identifier namespace at recall time",
            "seed": SEED,
            "arms": [
                run_arm("defect", work),
                run_arm("repaired", work),
                run_arm("no-engagement", work),
            ],
        }
    print(json.dumps(out, indent=2))
    res = Path(__file__).resolve().parent / "results"
    res.mkdir(exist_ok=True)
    (res / "exp12_learning_loop_ablation.json").write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
