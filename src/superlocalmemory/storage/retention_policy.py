# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""What bounds every table that only ever grows.

WHY A REGISTRY RATHER THAN FOUR MORE PRUNERS
--------------------------------------------
Three tables already had a pruner and each was written separately, wired
separately, and configured separately. The fourth unbounded table was found by
someone looking at a disk-usage report, and the fifth by someone looking at the
fourth. That is not a maintenance strategy, it is a sequence of accidents.

So the policy is declared here, per table, and one pass enforces all of them. A
table that appends without a policy is a defect the test suite can see, which is
the property none of the individual pruners could give us.

MEASURED, ON TWO REAL STORES
----------------------------
The live store is small enough that nothing looks urgent. The larger one shows
where each table is going:

    table                    live rows   larger store   what it was doing
    derivation_lineage         260,346         22,020   48.8 rows per memory,
                                                        690 for one of them
    temporal_events             16,837        118,210   70.7% pointing at
                                                        memories already deleted
    fact_access_log              5,388         90,759   34% older than 90 days
    consolidation_log            4,318         29,743   99.5% older than 30 days
    tool_events                  2,002         39,444   28 MB, no policy at all
    schema_version               3,496        234,348   SEVEN distinct versions

That last row is not a retention problem and a TTL would be the wrong fix for
it: the table has no unique constraint, so the six ``INSERT OR IGNORE`` call
sites that all believe they are idempotent append a duplicate every time. It is
registered here as ``NONE`` with that reason, and repaired by a migration.

WHAT EACH KIND MEANS, AND WHY THE CONJUNCTION EXISTS
----------------------------------------------------
``TTL_AND_CAP`` deletes a row only when it is BOTH older than the window AND
beyond the per-key cap. Either rule alone is unsafe here. A pure TTL on
``fact_access_log`` would delete the access history of a memory nobody has
touched for months -- which is precisely the memory whose history the decay
dynamics need. A pure cap would delete this morning's accesses on a busy
memory. The conjunction cannot destroy recent signal and cannot leave a key
unbounded, because time eventually satisfies the first clause for every row.

``CAP_PER_KEY`` without a window is right where old rows are *superseded* rather
than historical: re-deriving an object writes its lineage again, and the tenth
re-derivation of the same object tells you nothing the newest one does not.

``ORPHAN`` is for a table with no insertion timestamp. ``temporal_events`` has
``observation_date`` and ``referenced_date``, which are when the event happened,
not when the row was written -- so a TTL against either would delete a memory of
something long ago that was recorded this morning. Its bound is that the memory
it describes must still exist.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RetentionKind(Enum):
    """How a table is kept from growing without limit."""

    TTL_AND_CAP = "ttl_and_cap"
    TTL = "ttl"
    CAP_PER_KEY = "cap_per_key"
    ORPHAN = "orphan"
    #: Already pruned by a named module. Registered so the gate can see it is
    #: covered, and so the module is discoverable from here.
    EXTERNAL = "external"
    #: Bounded by construction, or a growth defect that a policy would mask. The
    #: reason is mandatory and is the whole value of the entry.
    NONE = "none"
    #: Measured growing faster than the store and the right rule is NOT yet
    #: known. This exists so the registry can be honest: writing NONE here would
    #: silence the gate on a table we know is a problem, and inventing a TTL for
    #: data whose lifecycle nobody has established is how a cleanup job deletes
    #: something load-bearing. An entry of this kind must carry its measurement.
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class RetentionPolicy:
    """One table's bound, with the reasoning attached to it."""

    table: str
    kind: RetentionKind
    reason: str
    ttl_days: int | None = None
    timestamp_column: str | None = None
    key_column: str | None = None
    cap_per_key: int | None = None
    #: ``(local_column, "referent_table.referent_column")`` for ORPHAN.
    referent: tuple[str, str] | None = None
    pruned_by: str | None = None
    #: Extra columns that, with ``key_column``, name one key. A cap counts
    #: "the newest N per key", so a key that is not unique across the whole
    #: table counts other rows' entries against a key's allowance. ``profile_id``
    #: is added automatically wherever the table has it and does not need to be
    #: listed here -- see ``_partition_columns``.
    also_partition_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(f"{self.table}: a policy without a reason is not a policy")
        if self.kind in (RetentionKind.TTL, RetentionKind.TTL_AND_CAP):
            if not self.ttl_days or not self.timestamp_column:
                raise ValueError(f"{self.table}: a time rule needs a window and a column")
        if self.kind in (RetentionKind.CAP_PER_KEY, RetentionKind.TTL_AND_CAP):
            if not self.cap_per_key or not self.key_column:
                raise ValueError(f"{self.table}: a cap rule needs a key and a limit")
        if self.kind is RetentionKind.ORPHAN and not self.referent:
            raise ValueError(f"{self.table}: an orphan rule needs a referent")
        if self.kind is RetentionKind.EXTERNAL and not self.pruned_by:
            raise ValueError(f"{self.table}: say which module prunes it")
        if self.kind is RetentionKind.UNRESOLVED and not any(
            character.isdigit() for character in self.reason
        ):
            raise ValueError(
                f"{self.table}: an unresolved entry must carry the measurement "
                f"that made it unresolved"
            )


REGISTERED_POLICIES: dict[str, RetentionPolicy] = {
    policy.table: policy
    for policy in (
        RetentionPolicy(
            table="derivation_lineage",
            kind=RetentionKind.CAP_PER_KEY,
            key_column="object_id",
            also_partition_by=("object_type",),
            cap_per_key=10,
            timestamp_column="created_at",
            reason=(
                "Provenance of a derived object. Re-deriving it appends another "
                "row, so the older ones are superseded rather than historical -- "
                "one object had 690. No time window: the newest ten answer every "
                "question the table is asked, at any age. The orphan half of this "
                "table's upkeep is lineage_retention.prune_orphan_lineage. The "
                "key is the object's id together with its type and workspace, "
                "because the id alone is unique in none of those directions."
            ),
        ),
        RetentionPolicy(
            table="fact_access_log",
            kind=RetentionKind.TTL_AND_CAP,
            ttl_days=90,
            timestamp_column="accessed_at",
            key_column="fact_id",
            cap_per_key=100,
            reason=(
                "Feeds tier demotion and the outcome signal, which both read "
                "recent access. Bounded by the conjunction so neither rule can "
                "take the signal on its own: a memory untouched for months keeps "
                "its history, and a busy memory keeps today's."
            ),
        ),
        RetentionPolicy(
            table="consolidation_log",
            kind=RetentionKind.TTL,
            ttl_days=30,
            timestamp_column="timestamp",
            reason=(
                "Read only as recent history -- the dashboard timeline and "
                "get_consolidation_history, which takes the newest 50. The column "
                "is `timestamp`; a rule written against `logged_at` would match "
                "nothing and quietly do nothing."
            ),
        ),
        RetentionPolicy(
            table="tool_events",
            kind=RetentionKind.TTL,
            ttl_days=180,
            timestamp_column="created_at",
            reason=(
                "Input to the assertion miner and the evolution triggers, which "
                "read recent activity. The window is long because no reader "
                "declares one and the cost of guessing short is a lost signal, "
                "not a bigger file."
            ),
        ),
        RetentionPolicy(
            table="temporal_events",
            kind=RetentionKind.ORPHAN,
            referent=("fact_id", "atomic_facts.fact_id"),
            reason=(
                "No insertion timestamp: observation_date and referenced_date are "
                "when the event happened, so a time rule would delete a memory of "
                "something long ago that was recorded today. Its bound is that the "
                "memory it describes still exists -- 70.7% of the rows on the "
                "larger store point at memories already deleted."
            ),
        ),
        RetentionPolicy(
            table="provenance",
            kind=RetentionKind.ORPHAN,
            referent=("fact_id", "atomic_facts.fact_id"),
            reason=(
                "Where a memory came from. 45.8% of the rows on the larger store "
                "(9,124 of 19,914) name a memory that no longer exists, so the "
                "cascade does not reach here. Bound is the memory's existence: "
                "provenance of a deleted memory is not provenance."
            ),
        ),
        RetentionPolicy(
            table="fact_context",
            kind=RetentionKind.ORPHAN,
            referent=("fact_id", "atomic_facts.fact_id"),
            reason=(
                "Generated description and keywords per memory. Tracks the memory "
                "count (2.94x against 2.27x more memories) but leaks on delete: "
                "3.9% orphaned on the larger store, 0.1% on the live one."
            ),
        ),
        RetentionPolicy(
            table="memory_events",
            kind=RetentionKind.TTL,
            ttl_days=180,
            timestamp_column="created_at",
            reason=(
                "An event log: 0% orphaned, so nothing leaks, but it grew 13.9x "
                "between two stores whose memory counts differ by 2.27x -- it "
                "tracks activity, not content. The window is long because no "
                "reader declares one."
            ),
        ),
        RetentionPolicy(
            table="action_outcomes",
            kind=RetentionKind.NONE,
            reason=(
                "MUST NOT be pruned. This is the feedback that teaches the ranker "
                "which answers were useful, and it is already sparse -- two rows "
                "in the whole store carry an informative signal, against the fifty "
                "needed to retrain. It grows 35.8x faster than the memory count, "
                "so it will need a rule eventually; taking one now would delete "
                "the evidence the ranker is waiting for."
            ),
        ),
        RetentionPolicy(
            table="compliance_audit",
            kind=RetentionKind.NONE,
            reason=(
                "The erasure audit trail. Two rows today. Its whole purpose is to "
                "outlive the data it describes, so bounding it by age would "
                "destroy the only record that an erasure happened."
            ),
        ),
        RetentionPolicy(
            table="atomic_facts",
            kind=RetentionKind.NONE,
            reason=(
                "These ARE the memories. What bounds them is the forgetting curve, "
                "tier demotion and erasure on request -- decisions about meaning, "
                "made elsewhere and visible to the user. A retention job here "
                "would delete someone's memories on a timer."
            ),
        ),
        RetentionPolicy(
            table="memories",
            kind=RetentionKind.NONE,
            reason=(
                "The raw records the memories are extracted from, and user data on "
                "the same footing. Grew 7.53x against 2.27x more memories because "
                "it tracks conversations rather than extracted facts, but the same "
                "argument holds: this is content, not bookkeeping."
            ),
        ),
        RetentionPolicy(
            table="embedding_metadata",
            kind=RetentionKind.NONE,
            reason=(
                "Exactly one row per memory -- 5,338 for 5,338 and 12,102 for "
                "12,102 on the two stores, and 0% orphaned. Bounded by the memory "
                "count by construction, and removed with the memory."
            ),
        ),
        RetentionPolicy(
            table="fact_entity_associations",
            kind=RetentionKind.NONE,
            reason=(
                "Which subjects each memory mentions. 3.16x against 2.27x more "
                "memories, and 0% orphaned on both stores, so it is bounded by the "
                "memory count and the cascade reaches it."
            ),
        ),
        RetentionPolicy(
            table="core_memory_blocks",
            kind=RetentionKind.NONE,
            reason=(
                "The always-present context blocks: 10 rows on one store and 20 on "
                "the other. Bounded by the number of block categories, which is a "
                "number we choose."
            ),
        ),
        RetentionPolicy(
            table="memory_scenes",
            kind=RetentionKind.UNRESOLVED,
            reason=(
                "Grew 27.15x between two stores whose memory counts differ by "
                "2.27x: 422 rows against 11,459. Keyed by a JSON list of memory "
                "ids rather than a column, so no single-column rule reaches it, "
                "and whether an old scene is superseded by a newer one or is "
                "history worth keeping has not been established. Naming that "
                "here rather than inventing a window for it."
            ),
        ),
        RetentionPolicy(
            table="mesh_events",
            kind=RetentionKind.UNRESOLVED,
            reason=(
                "Grew 65.83x between the two stores -- 6 rows against 395 -- the "
                "fastest of any table measured. Small in absolute terms because "
                "the mesh has never run with a second node, which is also why its "
                "real growth rate is unknown. A window guessed from 395 rows "
                "would be a guess."
            ),
        ),
        RetentionPolicy(
            table="soft_prompt_templates",
            kind=RetentionKind.CAP_PER_KEY,
            key_column="category",
            cap_per_key=5,
            timestamp_column="created_at",
            reason=(
                "Versioned: storing a prompt deactivates the previous one for its "
                "category and inserts a new row, so the count rises by one per "
                "category per consolidation cycle and never falls. 44 rows on the "
                "live store, of which 2 are active -- two categories times "
                "twenty-two cycles. Keeping five versions leaves room to see what "
                "changed without keeping every cycle forever."
            ),
        ),
        RetentionPolicy(
            table="rbac_sessions",
            kind=RetentionKind.TTL,
            ttl_days=30,
            timestamp_column="created_at",
            reason=(
                "Login sessions. 0 rows today because company mode has never run "
                "with users, which is exactly why it needs a rule before it does. "
                "Expired sessions are also purged once when the daemon starts, "
                "which is not a bound: a daemon that has been up for 25 hours "
                "accumulates for 25 hours. This is the recurring half."
            ),
        ),
        RetentionPolicy(
            table="skill_evolution_log",
            kind=RetentionKind.TTL,
            ttl_days=365,
            timestamp_column="created_at",
            reason=(
                "The record of automatic changes to an agent's own instructions. "
                "0 rows today. A year is deliberately long: this is the audit "
                "trail for a mutation a person approved, and it should outlive "
                "any question about why the behaviour changed."
            ),
        ),
        RetentionPolicy(
            table="ccq_audit_log",
            kind=RetentionKind.NONE,
            reason=(
                "An audit log, 0 rows today. Its purpose is to outlive what it "
                "describes, so bounding it by age would destroy the record rather "
                "than the storage cost."
            ),
        ),
        RetentionPolicy(
            table="projection_obligations",
            kind=RetentionKind.NONE,
            reason=(
                "Outstanding projection work: 1,305 rows on the live store and a "
                "row is removed when the obligation is discharged. Bounded by work "
                "in flight, and a rule here would drop unfinished work exactly the "
                "way a timeout on the outbox would."
            ),
        ),
        RetentionPolicy(
            table="projection_tombstones",
            kind=RetentionKind.NONE,
            reason=(
                "One row per deletion still to be applied to a projection -- 1 on "
                "the live store. Bounded by pending deletions, and dropping one "
                "would leave a deleted memory in a projection."
            ),
        ),
        RetentionPolicy(
            table="completion_manifests",
            kind=RetentionKind.NONE,
            reason=(
                "One per completed ingestion batch, 435 on the live store, removed "
                "with the batch it describes. Bounded by ingestion in flight."
            ),
        ),
        RetentionPolicy(
            table="fact_consolidations",
            kind=RetentionKind.NONE,
            reason=(
                "Which memories were merged into which: 2,432 rows against 5,338 "
                "memories. Bounded by the memory count, and the row is the only "
                "record that a merge happened, so age is the wrong axis."
            ),
        ),
        RetentionPolicy(
            table="consolidated_summaries",
            kind=RetentionKind.NONE,
            reason=(
                "One summary per group of memories, 1,137 on the live store. "
                "Bounded by the number of groups, which is bounded by the memory "
                "count; a summary is replaced rather than appended."
            ),
        ),
        RetentionPolicy(
            table="ingestion_operations",
            kind=RetentionKind.NONE,
            reason=(
                "742 rows on the live store and 117 on the larger one -- it went "
                "DOWN as the store grew, so something already collects it. "
                "Bounded, and adding a second rule would race the first."
            ),
        ),
        RetentionPolicy(
            table="correction_cases",
            kind=RetentionKind.NONE,
            reason=(
                "One per correction a person proposed, 90 on the live store. This "
                "is the ledger of who changed what and why; deleting an old case "
                "removes the answer to a question about a memory that still exists."
            ),
        ),
        RetentionPolicy(
            table="behavioral_assertions",
            kind=RetentionKind.NONE,
            reason=(
                "What the system has concluded about how it is used: 9 rows. "
                "Reinforced in place rather than appended, so bounded by the number "
                "of distinct conclusions."
            ),
        ),
        RetentionPolicy(
            table="feedback_records",
            kind=RetentionKind.NONE,
            reason=(
                "Explicit feedback on an answer, 1 row. Sparse and load-bearing "
                "for ranking quality; the same argument as the outcome feedback "
                "above, at an even smaller count."
            ),
        ),
        RetentionPolicy(
            table="profiles",
            kind=RetentionKind.NONE,
            reason=(
                "2 rows. Bounded by the number of workspaces a person creates, and "
                "removed by erasure rather than by age."
            ),
        ),
        RetentionPolicy(
            table="polar_embeddings",
            kind=RetentionKind.NONE,
            reason=(
                "0 rows; an alternate vector representation, at most one per "
                "memory when written. Bounded by the memory count by construction."
            ),
        ),
        RetentionPolicy(
            table="embedding_quantization_metadata",
            kind=RetentionKind.NONE,
            reason=(
                "0 rows; at most one per memory when quantization runs. Bounded by "
                "the memory count by construction."
            ),
        ),
        RetentionPolicy(
            table="ccq_consolidated_blocks",
            kind=RetentionKind.NONE,
            reason=(
                "0 rows; a compiled block is replaced rather than appended, so it "
                "is bounded by the number of block categories."
            ),
        ),
        RetentionPolicy(
            table="backup_destinations",
            kind=RetentionKind.NONE,
            reason=(
                "0 rows; one per configured backup target. Bounded by a number the "
                "user chooses, and a destination does not expire."
            ),
        ),
        RetentionPolicy(
            table="retention_rules",
            kind=RetentionKind.NONE,
            reason=(
                "0 rows; per-memory retention rules for compliance, one per rule a "
                "person writes. Bounded by that, and a rule that expired would "
                "silently stop being enforced."
            ),
        ),
        RetentionPolicy(
            table="mesh_messages",
            kind=RetentionKind.UNRESOLVED,
            reason=(
                "0 rows, because the mesh has never run with a second node -- so "
                "its growth rate is unmeasured, exactly like mesh_events at 65.83x "
                "between two stores. A message is presumably consumed and "
                "removable, but presumably is not a rule."
            ),
        ),
        RetentionPolicy(
            table="mesh_sent_ops",
            kind=RetentionKind.UNRESOLVED,
            reason=(
                "0 rows, same reason as the other mesh tables: it records "
                "operations already sent, which sounds collectable once "
                "acknowledged, but with 0 rows and no second node there is nothing "
                "to measure an acknowledgement window against."
            ),
        ),
        RetentionPolicy(
            table="rbac_users",
            kind=RetentionKind.NONE,
            reason=(
                "0 rows; one per member of a workspace. Bounded by team size, and "
                "removed when a person is removed, not when their row gets old."
            ),
        ),
        RetentionPolicy(
            table="graph_edges",
            kind=RetentionKind.EXTERNAL,
            pruned_by="core.graph_pruner.prune_graph",
            reason="Orphans, duplicates, weak edges and hub degree, every cycle.",
        ),
        RetentionPolicy(
            table="association_edges",
            kind=RetentionKind.EXTERNAL,
            pruned_by="core.graph_pruner.prune_graph",
            reason="Orphan sweep in the same pass as graph_edges.",
        ),
        RetentionPolicy(
            table="activation_cache",
            kind=RetentionKind.EXTERNAL,
            pruned_by="storage.database.DatabaseManager.cleanup_activation_cache",
            reason="Expiry-based GC once per maintenance cycle.",
        ),
        RetentionPolicy(
            table="schema_version",
            kind=RetentionKind.NONE,
            reason=(
                "Should hold one row per schema version and holds thousands -- "
                "3,496 rows for 7 versions on one store, 234,348 on another. The "
                "table has no unique constraint, so the six INSERT OR IGNORE call "
                "sites that believe they are idempotent each append a duplicate. "
                "A retention rule here would hide a correctness bug behind a "
                "cleanup job; the unique index is the fix."
            ),
        ),
        RetentionPolicy(
            table="migration_log",
            kind=RetentionKind.NONE,
            reason=(
                "One row per migration, and there are 48. Bounded by the number "
                "of migrations ever written, which is a number we control."
            ),
        ),
        RetentionPolicy(
            table="projection_outbox",
            kind=RetentionKind.NONE,
            reason=(
                "Keyed on fact_id, so it cannot exceed the fact count, and a row "
                "is deleted when the projection accepts it. Bounded by design; a "
                "TTL here would silently drop unprojected work."
            ),
        ),
    )
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names, or empty when the table cannot be introspected.

    A virtual table raises rather than answering when its module is not loaded
    on this connection -- ``sqlite_vec`` is loaded per connection, so a
    maintenance connection that never asked for it gets "no such module: vec0"
    from a plain PRAGMA. An unreadable table is one this pass leaves alone,
    which is the right answer for a vector index anyway: it is derived from a
    base table and bounded by writing to it.
    """
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error as exc:
        logger.debug("retention: cannot introspect %s: %s", table, exc)
        return set()


def apply_policy(
    conn: sqlite3.Connection, policy: RetentionPolicy, *, dry_run: bool = False
) -> int:
    """Enforce one policy. Returns rows deleted, or would-be deleted.

    A policy whose table or column is absent is a no-op, not an error: these
    tables arrive with migrations and a store may predate any of them.
    """
    # A kind with no rule of its own returns before any SQL is built. Without
    # UNRESOLVED in this set it fell through to the cap branch with every field
    # None and emitted "no such column: None" on two real stores -- and the test
    # meant to catch that passed anyway, because run_retention swallows a
    # per-table error and the table was simply absent from the result either way.
    if policy.kind in (
        RetentionKind.EXTERNAL, RetentionKind.NONE, RetentionKind.UNRESOLVED,
    ):
        return 0
    if not _table_exists(conn, policy.table):
        return 0
    present = _columns(conn, policy.table)
    needed = [
        column
        for column in (policy.timestamp_column, policy.key_column)
        if column is not None
    ]
    if policy.referent is not None:
        needed.append(policy.referent[0])
    missing = [column for column in needed if column not in present]
    if missing:
        logger.debug(
            "retention: %s has no %s; skipping", policy.table, ", ".join(missing)
        )
        return 0

    if policy.kind is RetentionKind.ORPHAN:
        local, target = policy.referent  # type: ignore[misc]
        referent_table, referent_column = target.split(".", 1)
        if not _table_exists(conn, referent_table):
            return 0
        where = (
            f"{local} IS NOT NULL AND {local} NOT IN "
            f"(SELECT {referent_column} FROM {referent_table})"
        )
        params: tuple[Any, ...] = ()
    elif policy.kind is RetentionKind.TTL:
        where = f"{policy.timestamp_column} < datetime('now', ?)"
        params = (f"-{policy.ttl_days} days",)
    else:
        # Beyond the cap AND (for TTL_AND_CAP) older than the window. The window
        # function ranks newest-first per key so "beyond the cap" means "not one
        # of the newest N", which is the only reading that keeps recent rows.
        age = ""
        params = ()
        if policy.kind is RetentionKind.TTL_AND_CAP:
            age = f" AND {policy.timestamp_column} < datetime('now', ?)"
            params = (f"-{policy.ttl_days} days",)
        partition = ", ".join(_partition_columns(conn, policy))
        where = (
            f"rowid IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER ("
            f"PARTITION BY {partition} "
            f"ORDER BY {policy.timestamp_column} DESC, rowid DESC) AS rn "
            f"FROM {policy.table}) WHERE rn > {int(policy.cap_per_key or 0)})"
            f"{age}"
        )

    if dry_run:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {policy.table} WHERE {where}", params
        ).fetchone()
        return int(row[0] if row else 0)

    cursor = conn.execute(f"DELETE FROM {policy.table} WHERE {where}", params)
    return int(cursor.rowcount or 0)


def _partition_columns(
    conn: sqlite3.Connection, policy: RetentionPolicy
) -> tuple[str, ...]:
    """Every column that has to match for two rows to be under the same cap.

    A cap keeps the newest N rows per key, so anything the key does not
    distinguish gets counted against somebody else's allowance. Two workspaces
    are the case that matters: a shared table's ids are unique per workspace,
    not across the store, so partitioning on the id alone lets one workspace's
    rows evict another's. Nothing on a single-workspace store notices, which is
    why this has to be structural rather than something a policy author
    remembers.

    ``profile_id`` is added wherever the table has it, checked against the live
    schema rather than assumed, because these policies also run on stores old
    enough to predate the column.
    """
    columns: list[str] = []
    if _has_column(conn, policy.table, "profile_id"):
        columns.append("profile_id")
    for extra in policy.also_partition_by:
        if _has_column(conn, policy.table, extra) and extra not in columns:
            columns.append(extra)
    if policy.key_column and policy.key_column not in columns:
        columns.append(policy.key_column)
    return tuple(columns)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Whether ``table`` has ``column`` on this store."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return False
    return any(str(row[1]) == column for row in rows)


def run_retention(
    conn: sqlite3.Connection, *, dry_run: bool = False
) -> dict[str, int]:
    """Enforce every policy that has one. Returns rows removed per table.

    One statement per table, each committed by the caller's connection. A table
    that fails does not stop the others: an unbounded table is a slow problem and
    stopping the pass would leave every table after it in the dict unbounded too.
    """
    removed: dict[str, int] = {}
    for policy in REGISTERED_POLICIES.values():
        try:
            count = apply_policy(conn, policy, dry_run=dry_run)
        except sqlite3.Error as exc:
            logger.warning("retention: %s failed: %s", policy.table, exc)
            continue
        if count:
            removed[policy.table] = count
    return removed


def undeclared_growing_tables(conn: sqlite3.Connection) -> list[str]:
    """Tables that look append-shaped and have no policy.

    "Append-shaped" is taken from the schema, not from a hand-written list, so a
    table added next release shows up here without anyone remembering to add it:
    it carries a timestamp-ish column and is not a registered policy, an FTS
    shadow, or SQLite's own bookkeeping.
    """
    candidates: list[str] = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    for (name,) in rows:
        if name in REGISTERED_POLICIES or name.startswith("sqlite_"):
            continue
        # FTS5 external-content shadow tables are derived from their base table
        # and are pruned by writing to it.
        if any(
            name.endswith(suffix)
            for suffix in ("_data", "_idx", "_content", "_docsize", "_config")
        ):
            continue
        columns = _columns(conn, name)
        if not columns & {
            "created_at", "accessed_at", "occurred_at", "timestamp", "logged_at",
        }:
            continue
        candidates.append(name)
    return candidates
