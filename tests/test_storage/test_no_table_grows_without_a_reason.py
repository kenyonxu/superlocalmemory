# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Every table that only appends is either bounded or says why it is not.

Three tables had a pruner each, written separately and wired separately. The
fourth unbounded table was found by someone reading a disk-usage report, and the
fifth by someone reading the fourth. Measured on two real stores:

    table                live rows   larger store
    derivation_lineage     260,346         22,020   48.8 per memory, 690 for one
    temporal_events         16,837        118,210   70.7% referencing deleted memories
    fact_access_log          5,388         90,759   34% older than 90 days
    consolidation_log        4,318         29,743   99.5% older than 30 days
    tool_events              2,002         39,444   28 MB, no policy at all
    schema_version           3,496        234,348   for SEVEN distinct versions

The registry is the fix, and the last test here is the reason the registry
exists: it fails when a new table appears with no policy, which is the only
mechanism that catches the sixth one before a disk-usage report does.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage.retention_policy import (
    REGISTERED_POLICIES,
    RetentionKind,
    RetentionPolicy,
    apply_policy,
    run_retention,
    undeclared_growing_tables,
)


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def _rows(connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestACapKeepsTheNewestAndNothingElse:
    """`derivation_lineage`: re-deriving an object supersedes the older record."""

    def test_only_the_newest_rows_per_key_survive(self, conn) -> None:
        conn.execute(
            "CREATE TABLE derivation_lineage ("
            " lineage_id INTEGER PRIMARY KEY, object_id TEXT, created_at TEXT)"
        )
        for index in range(40):
            conn.execute(
                "INSERT INTO derivation_lineage (object_id, created_at) VALUES (?, ?)",
                ("obj-a", f"2026-01-{index % 28 + 1:02d}T00:00:0{index % 10}"),
            )
        for index in range(3):
            conn.execute(
                "INSERT INTO derivation_lineage (object_id, created_at) VALUES (?, ?)",
                ("obj-b", f"2026-02-0{index + 1}T00:00:00"),
            )
        conn.commit()

        removed = apply_policy(conn, REGISTERED_POLICIES["derivation_lineage"])

        assert removed == 30
        assert _rows(conn, "derivation_lineage") == 13
        per_key = dict(
            conn.execute(
                "SELECT object_id, COUNT(*) FROM derivation_lineage GROUP BY object_id"
            )
        )
        assert per_key == {"obj-a": 10, "obj-b": 3}, (
            "the cap is per key: a key under the cap must lose nothing"
        )

    def test_it_keeps_the_newest_not_whichever_rows_come_first(self, conn) -> None:
        """A cap that kept the oldest would pass a row count and be backwards."""
        conn.execute(
            "CREATE TABLE derivation_lineage ("
            " lineage_id INTEGER PRIMARY KEY, object_id TEXT, created_at TEXT)"
        )
        for day in range(1, 16):
            conn.execute(
                "INSERT INTO derivation_lineage (object_id, created_at) VALUES (?, ?)",
                ("obj", f"2026-03-{day:02d}T00:00:00"),
            )
        conn.commit()

        apply_policy(conn, REGISTERED_POLICIES["derivation_lineage"])

        kept = [
            row[0]
            for row in conn.execute(
                "SELECT created_at FROM derivation_lineage ORDER BY created_at"
            )
        ]
        assert kept[0] == "2026-03-06T00:00:00"
        assert kept[-1] == "2026-03-15T00:00:00"


class TestTheConjunctionProtectsRecentSignal:
    """`fact_access_log` feeds tier demotion and the outcome signal."""

    @pytest.fixture()
    def access_log(self, conn):
        conn.execute(
            "CREATE TABLE fact_access_log ("
            " log_id INTEGER PRIMARY KEY, fact_id TEXT, accessed_at TEXT)"
        )
        return conn

    def test_an_untouched_memory_keeps_its_whole_history(self, access_log) -> None:
        """A pure TTL would delete exactly the history the decay dynamics need.

        The memory nobody has touched for months is the one whose access history
        decides whether it is demoted. Deleting it because it is old inverts the
        purpose of keeping it.
        """
        for index in range(5):
            access_log.execute(
                "INSERT INTO fact_access_log (fact_id, accessed_at) VALUES (?, ?)",
                ("cold-fact", "2024-01-01T00:00:00"),
            )
        access_log.commit()

        removed = apply_policy(access_log, REGISTERED_POLICIES["fact_access_log"])

        assert removed == 0
        assert _rows(access_log, "fact_access_log") == 5

    def test_a_busy_memory_keeps_todays_accesses(self, access_log) -> None:
        """A pure cap would delete this morning's rows on a busy memory."""
        for index in range(150):
            access_log.execute(
                "INSERT INTO fact_access_log (fact_id, accessed_at) "
                "VALUES (?, datetime('now'))",
                ("hot-fact",),
            )
        access_log.commit()

        removed = apply_policy(access_log, REGISTERED_POLICIES["fact_access_log"])

        assert removed == 0, "nothing here is old enough to delete"
        assert _rows(access_log, "fact_access_log") == 150

    def test_old_rows_beyond_the_cap_are_the_ones_that_go(self, access_log) -> None:
        for index in range(120):
            access_log.execute(
                "INSERT INTO fact_access_log (fact_id, accessed_at) "
                "VALUES (?, datetime('now', '-200 days'))",
                ("stale-fact",),
            )
        for index in range(3):
            access_log.execute(
                "INSERT INTO fact_access_log (fact_id, accessed_at) "
                "VALUES (?, datetime('now'))",
                ("stale-fact",),
            )
        access_log.commit()

        removed = apply_policy(access_log, REGISTERED_POLICIES["fact_access_log"])

        assert removed == 23, "123 rows, cap 100, and only the old ones qualify"
        assert _rows(access_log, "fact_access_log") == 100
        recent = access_log.execute(
            "SELECT COUNT(*) FROM fact_access_log "
            "WHERE accessed_at > datetime('now', '-1 day')"
        ).fetchone()[0]
        assert recent == 3, "the newest rows must survive regardless of the cap"


class TestATimeRuleNeedsTheRightColumn:
    def test_stale_consolidation_entries_go(self, conn) -> None:
        conn.execute(
            "CREATE TABLE consolidation_log ("
            " action_id TEXT PRIMARY KEY, profile_id TEXT, timestamp TEXT)"
        )
        for index in range(10):
            conn.execute(
                "INSERT INTO consolidation_log VALUES (?, 'default', "
                "datetime('now', '-60 days'))", (f"old-{index}",),
            )
        for index in range(4):
            conn.execute(
                "INSERT INTO consolidation_log VALUES (?, 'default', datetime('now'))",
                (f"new-{index}",),
            )
        conn.commit()

        removed = apply_policy(conn, REGISTERED_POLICIES["consolidation_log"])

        assert removed == 10
        assert _rows(conn, "consolidation_log") == 4

    def test_the_policy_names_the_column_the_table_actually_has(self) -> None:
        """The plan's rule was written against `logged_at`, which does not exist.

        A DELETE naming a missing column raises, and this pass swallows per-table
        errors so the others still run -- so the rule would have looked like it
        was working and removed nothing, forever.
        """
        assert REGISTERED_POLICIES["consolidation_log"].timestamp_column == "timestamp"

    def test_a_missing_column_is_a_no_op_not_a_crash(self, conn) -> None:
        """These tables arrive with migrations; a store may predate any of them."""
        conn.execute("CREATE TABLE consolidation_log (action_id TEXT PRIMARY KEY)")
        conn.commit()

        assert apply_policy(conn, REGISTERED_POLICIES["consolidation_log"]) == 0


class TestAnOrphanRuleForATableWithNoClock:
    def test_rows_whose_memory_is_gone_are_removed(self, conn) -> None:
        """`temporal_events` has no insertion timestamp.

        Its dates are when the event happened, so a TTL would delete a memory of
        something long ago that was recorded this morning. Its bound is that the
        memory it describes still exists.
        """
        conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE temporal_events ("
            " event_id INTEGER PRIMARY KEY, fact_id TEXT, observation_date TEXT)"
        )
        conn.execute("INSERT INTO atomic_facts VALUES ('alive')")
        conn.execute(
            "INSERT INTO temporal_events (fact_id, observation_date) "
            "VALUES ('alive', '1999-01-01')"
        )
        conn.execute(
            "INSERT INTO temporal_events (fact_id, observation_date) "
            "VALUES ('deleted', '2026-08-01')"
        )
        conn.commit()

        removed = apply_policy(conn, REGISTERED_POLICIES["temporal_events"])

        assert removed == 1
        survivors = [
            row[0] for row in conn.execute("SELECT fact_id FROM temporal_events")
        ]
        assert survivors == ["alive"], (
            "the 1999 row survives and the 2026 one goes: age is not the rule"
        )

    def test_the_policy_is_not_a_time_rule(self) -> None:
        policy = REGISTERED_POLICIES["temporal_events"]
        assert policy.kind is RetentionKind.ORPHAN
        assert policy.ttl_days is None


class TestThePassIsHonestAboutWhatItDid:
    def test_a_dry_run_counts_without_deleting(self, conn) -> None:
        conn.execute(
            "CREATE TABLE consolidation_log (action_id TEXT PRIMARY KEY, timestamp TEXT)"
        )
        for index in range(6):
            conn.execute(
                "INSERT INTO consolidation_log VALUES (?, datetime('now','-90 days'))",
                (f"x{index}",),
            )
        conn.commit()

        counted = apply_policy(
            conn, REGISTERED_POLICIES["consolidation_log"], dry_run=True
        )

        assert counted == 6
        assert _rows(conn, "consolidation_log") == 6

    def test_one_failing_table_does_not_stop_the_others(self, conn) -> None:
        """An unbounded table is a slow problem; stopping the pass makes it wide.

        Every table after the failure would stay unbounded, and the log would
        name only the first one.
        """
        conn.execute(
            "CREATE TABLE consolidation_log (action_id TEXT PRIMARY KEY, timestamp TEXT)"
        )
        conn.execute(
            "INSERT INTO consolidation_log VALUES ('a', datetime('now','-90 days'))"
        )
        # A table with the right name and a hostile shape: the timestamp column
        # is there, so the policy engages, but the table is a view and cannot be
        # deleted from.
        conn.execute("CREATE TABLE base (fact_id TEXT, created_at TEXT)")
        conn.execute("CREATE VIEW tool_events AS SELECT fact_id, created_at FROM base")
        conn.commit()

        removed = run_retention(conn)

        assert removed.get("consolidation_log") == 1, (
            "the table after the failing one was still processed"
        )

    def test_a_table_pruned_elsewhere_is_registered_and_left_alone(self, conn) -> None:
        """The registry records who prunes a table even when it is not this pass."""
        policy = REGISTERED_POLICIES["graph_edges"]
        assert policy.kind is RetentionKind.EXTERNAL
        assert policy.pruned_by == "core.graph_pruner.prune_graph"
        assert apply_policy(conn, policy) == 0


class TestEveryPolicyIsAPolicy:
    def test_a_policy_without_a_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a policy"):
            RetentionPolicy(table="t", kind=RetentionKind.NONE, reason="  ")

    def test_a_time_rule_without_a_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="window"):
            RetentionPolicy(table="t", kind=RetentionKind.TTL, reason="because")

    def test_a_cap_rule_without_a_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="key"):
            RetentionPolicy(
                table="t", kind=RetentionKind.CAP_PER_KEY, reason="because",
                cap_per_key=10,
            )

    def test_every_registered_policy_explains_itself(self) -> None:
        for policy in REGISTERED_POLICIES.values():
            assert len(policy.reason) > 40, (
                f"{policy.table}: a one-line reason is a label, not a reason"
            )

    def test_a_table_with_no_rule_says_which_kind_of_no_rule(self) -> None:
        """There are two honest reasons to have no rule, and they are different.

        Either the table cannot grow without bound -- one row per memory, one row
        per version -- or it can and pruning it would be wrong anyway, because
        the rows are the user's memories, the audit trail of an erasure, or a
        learning signal that is already too sparse to lose. Collapsing the two
        would let "we did not think about it" pass as either.
        """
        bounded = ("bounded", "one row", "unique", "cannot exceed", "number we choose",
                   "number we control", "by construction")
        must_not = ("must not", "would delete", "would destroy", "outlive",
                    "belongs to", "content, not", "removes the", "would leave",
                    "silently stop", "load-bearing", "only record", "wrong axis")
        for policy in REGISTERED_POLICIES.values():
            if policy.kind is not RetentionKind.NONE:
                continue
            reason = policy.reason.lower()
            says_bounded = any(word in reason for word in bounded)
            says_must_not = any(word in reason for word in must_not)
            assert says_bounded or says_must_not, (
                f"{policy.table}: has no rule and does not say whether that is "
                f"because it cannot grow or because pruning it would be wrong"
            )


class TestTheGateThatCatchesTheNextTable:
    """The reason the registry exists at all."""

    def test_a_new_appending_table_with_no_policy_is_reported(self, conn) -> None:
        conn.execute(
            "CREATE TABLE some_new_event_log ("
            " id INTEGER PRIMARY KEY, payload TEXT, created_at TEXT)"
        )
        conn.commit()

        undeclared = undeclared_growing_tables(conn)

        assert "some_new_event_log" in undeclared

    def test_a_registered_table_is_not_reported(self, conn) -> None:
        conn.execute(
            "CREATE TABLE consolidation_log (action_id TEXT PRIMARY KEY, timestamp TEXT)"
        )
        conn.commit()

        assert undeclared_growing_tables(conn) == []

    def test_a_table_with_no_clock_is_not_mistaken_for_a_log(self, conn) -> None:
        """A lookup table appends too, but bounding it by age means nothing."""
        conn.execute("CREATE TABLE entity_aliases (alias TEXT, entity_id TEXT)")
        conn.commit()

        assert undeclared_growing_tables(conn) == []

    def test_full_text_shadow_tables_are_not_flagged(self, conn) -> None:
        """They are derived from their base table and pruned by writing to it."""
        conn.execute("CREATE TABLE thing_fts_data (id INTEGER, created_at TEXT)")
        conn.execute("CREATE TABLE thing_fts_docsize (id INTEGER, created_at TEXT)")
        conn.commit()

        assert undeclared_growing_tables(conn) == []


class TestAnUnresolvedTableIsSaidOutLoud:
    """Writing NONE for a table we know is growing would silence the gate."""

    def test_an_unresolved_entry_must_carry_its_measurement(self) -> None:
        with pytest.raises(ValueError, match="measurement"):
            RetentionPolicy(
                table="t",
                kind=RetentionKind.UNRESOLVED,
                reason="this one grows quickly and we have not decided what to do",
            )

    def test_the_unresolved_entries_name_numbers(self) -> None:
        unresolved = [
            policy
            for policy in REGISTERED_POLICIES.values()
            if policy.kind is RetentionKind.UNRESOLVED
        ]
        assert unresolved, (
            "if nothing is unresolved any more, the growth was measured and "
            "given a rule — say so by changing the kind, not by deleting this"
        )
        for policy in unresolved:
            # A measurement, which is what makes the entry honest rather than a
            # shrug. It may be a growth multiple ("65.83x") or a count ("0 rows,
            # because the feature has never run") -- the second is as real a
            # measurement as the first, and is why the rate is unknown.
            assert any(character.isdigit() for character in policy.reason), (
                f"{policy.table}: unresolved without a number in it"
            )

    def test_an_unresolved_table_is_not_swept_by_accident(self, conn) -> None:
        """It has no rule yet. The pass must leave it entirely alone."""
        conn.execute(
            "CREATE TABLE memory_scenes ("
            " scene_id TEXT PRIMARY KEY, fact_ids_json TEXT, created_at TEXT)"
        )
        for index in range(20):
            conn.execute(
                "INSERT INTO memory_scenes VALUES (?, '[]', datetime('now','-900 days'))",
                (f"scene-{index}",),
            )
        conn.commit()

        # Absence from the result dict proves nothing on its own: a policy that
        # raised is also absent, because run_retention swallows per-table errors
        # so one bad table cannot stop the rest. That is exactly how the first
        # version of this test passed while the pass was emitting "no such
        # column: None". So assert on the call that builds the SQL.
        assert apply_policy(conn, REGISTERED_POLICIES["memory_scenes"]) == 0
        assert (
            apply_policy(conn, REGISTERED_POLICIES["memory_scenes"], dry_run=True) == 0
        ), "a dry run must not build a rule for a table that has none"

        removed = run_retention(conn)

        assert "memory_scenes" not in removed
        assert _rows(conn, "memory_scenes") == 20


class TestTheMemoriesThemselvesAreNotSwept:
    """The registry covers `atomic_facts`, and its policy is to do nothing."""

    def test_the_memory_table_has_a_policy_and_the_policy_is_no_rule(self) -> None:
        policy = REGISTERED_POLICIES["atomic_facts"]
        assert policy.kind is RetentionKind.NONE
        assert "forgetting" in policy.reason.lower()

    def test_no_rule_can_delete_a_memory(self, conn) -> None:
        """A retention pass that touched atomic_facts would be deleting someone's
        memories on a timer, which is a decision about meaning and belongs to the
        forgetting curve and to erasure on request."""
        conn.execute(
            "CREATE TABLE atomic_facts ("
            " fact_id TEXT PRIMARY KEY, content TEXT, created_at TEXT)"
        )
        for index in range(12):
            conn.execute(
                "INSERT INTO atomic_facts VALUES (?, ?, datetime('now','-2000 days'))",
                (f"old-{index}", "a memory from years ago"),
            )
        conn.commit()

        run_retention(conn)

        assert _rows(conn, "atomic_facts") == 12

    def test_the_feedback_that_teaches_the_ranker_is_not_swept(self, conn) -> None:
        """It is already sparse: two informative rows against the fifty needed."""
        policy = REGISTERED_POLICIES["action_outcomes"]
        assert policy.kind is RetentionKind.NONE
        assert "MUST NOT" in policy.reason

        conn.execute(
            "CREATE TABLE action_outcomes ("
            " outcome_id TEXT PRIMARY KEY, outcome TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO action_outcomes VALUES ('o1','used',datetime('now','-3000 days'))"
        )
        conn.commit()

        run_retention(conn)

        assert _rows(conn, "action_outcomes") == 1

    def test_the_erasure_audit_trail_outlives_what_it_describes(self, conn) -> None:
        policy = REGISTERED_POLICIES["compliance_audit"]
        assert policy.kind is RetentionKind.NONE
        assert "outlive" in policy.reason


class TestProvenanceOfADeletedMemoryIsNotProvenance:
    """45.8% of these rows on a real store name a memory that is gone."""

    def test_orphaned_provenance_is_removed(self, conn) -> None:
        conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE provenance ("
            " provenance_id INTEGER PRIMARY KEY, fact_id TEXT, created_at TEXT)"
        )
        conn.execute("INSERT INTO atomic_facts VALUES ('kept')")
        conn.execute(
            "INSERT INTO provenance (fact_id, created_at) VALUES ('kept', datetime('now'))"
        )
        for index in range(4):
            conn.execute(
                "INSERT INTO provenance (fact_id, created_at) "
                "VALUES (?, datetime('now'))", (f"gone-{index}",),
            )
        conn.commit()

        removed = apply_policy(conn, REGISTERED_POLICIES["provenance"])

        assert removed == 4
        assert _rows(conn, "provenance") == 1

    def test_provenance_of_a_living_memory_survives_any_age(self, conn) -> None:
        """The rule is existence, not age: old provenance is still provenance."""
        conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE provenance ("
            " provenance_id INTEGER PRIMARY KEY, fact_id TEXT, created_at TEXT)"
        )
        conn.execute("INSERT INTO atomic_facts VALUES ('ancient')")
        conn.execute(
            "INSERT INTO provenance (fact_id, created_at) "
            "VALUES ('ancient', '2019-01-01T00:00:00')"
        )
        conn.commit()

        assert apply_policy(conn, REGISTERED_POLICIES["provenance"]) == 0


class TestAnUnreadableTableIsLeftAlone:
    def test_a_virtual_table_whose_module_is_absent_does_not_break_the_pass(
        self, conn,
    ) -> None:
        """`sqlite_vec` loads per connection, so a maintenance connection that
        never asked for it gets "no such module: vec0" from a plain PRAGMA. That
        crashed the whole sweep on a real store.

        The table below is registered as one whose module this connection cannot
        load, so reading its shape raises exactly as it does in production. What
        has to survive that is the rest of the sweep — asserting only that a
        list came back was true whether or not anything else worked.
        """
        conn.execute(
            "CREATE TABLE consolidation_log (action_id TEXT PRIMARY KEY, timestamp TEXT)"
        )
        conn.execute(
            "INSERT INTO consolidation_log VALUES ('a', datetime('now','-90 days'))"
        )
        conn.commit()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS an_unbounded_table (id INTEGER, created_at TEXT)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS vec_like_index (id INTEGER)")
        conn.commit()

        assert run_retention(conn).get("consolidation_log") == 1

        # A table whose shape cannot be read must not hide the tables that can.
        class _RefusesOneTable:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *args):
                if "vec_like_index" in str(sql):
                    raise sqlite3.OperationalError("no such module: vec0")
                return self._inner.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        undeclared = undeclared_growing_tables(_RefusesOneTable(conn))

        assert "an_unbounded_table" in undeclared, (
            "one unreadable table silenced the report for every other table, "
            "which is how an unbounded table goes unnoticed"
        )
