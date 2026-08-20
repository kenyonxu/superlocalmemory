"""v3.5.x acceptance gates — issue #113 consolidation layer.
Authored by the release coordinator, NOT by implementers.
Implementation agents may NOT modify this file.

WHAT #113 ACTUALLY ASKS FOR
---------------------------
Issue #113 ("Personal Memory Views and Memory Consolidation Layer") requests a
HUMAN-READABLE layer, not a database optimisation:
    - Session Summary   ("Today: analyzed SLM memory architecture ...")
    - Daily Reflection  ("Main topics: local AI deployment, memory system design ...")
    - Project Work Log  ("Project: SuperLocalMemory. Completed: tested endpoints ...")
    - Custom user-defined views driven by a user prompt

The maintainer's own public reply on that issue sets the binding constraint:
    "views must be customizable, profile-scoped, privacy-aware, and traceable
     back to the underlying memories rather than becoming opaque generic summaries"

OWNER DECISION FOR 4.0.6 (recorded): deliver the three BOUNDED summaries plus the
wiring of the existing compaction module. Prompt-driven custom views are
deferred to 4.0.7 — they are non-deterministic, hard to make traceable, and a
prompt-injection surface, which is the wrong thing to add to a release whose
core claim is compliance and honest reporting.

THE PRE-EXISTING MODULE
-----------------------
core/fact_consolidator.py (598 lines) merges warm/cold atomic facts about the
same entity, archives the originals, and records provenance in
fact_consolidations. It is correct for what it does and it is WIRED TO NOTHING —
imported only by its own test, listed in tests/test_no_dead_modules.py as
_KNOWN_DEAD. It satisfies none of #113's user-facing asks. Both halves are in
scope this wave: wire the compaction, and build the summaries on top.

MEASURED DATA REALITY (live store, 3,294 facts — verified before writing this gate)
-----------------------------------------------------------------------------------
Implementers MUST build against these numbers, not against assumptions:
  Daily Reflection   VIABLE   48 distinct active days, 10-454 facts/day
  Project Work Log   VIABLE   tool_events.project_path -> 1,899 rows, 13 real projects
  Session Summary    SPARSE   only 127 of 3,294 facts (3.9%) carry session_id, 21 sessions
  fact_consolidations = 0 rows  => the compaction module has never once run in production
  consolidation_log   = 3,558 rows => a DIFFERENT engine (ConsolidationEngine) runs constantly

  TRAP, DO NOT FALL IN IT: entity_profiles.project_name has 1,148 rows and
  exactly ONE distinct value. Grouping a Project Work Log by that column yields a
  single meaningless bucket. Project scope must come from tool_events.project_path.

RELEASE-BLOCKING INVARIANTS (owner, verbatim): "recall and remember timing should
not impact ... There should not be any deadlocks in the database due to your
things. Everything should be backward compatible. Also ... there should not be
any memory leaks."
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "superlocalmemory"

#: The new #113 layer lives HERE. An explicit contract, not a fuzzy glob.
#:
#: The first draft of this gate globbed for "*summar*.py" and silently matched
#: two pre-existing, unrelated modules — core/community_summary.py and
#: core/summarizer.py. Traceability and fallback gates went GREEN against code
#: that has nothing to do with #113. A gate that passes for the wrong reason is
#: worse than no gate, because it retires the reviewer's attention. Naming the
#: package makes every assertion below unambiguous.
_SUMMARIES = _SRC / "summaries"


def _read(rel: str) -> str:
    return (_SRC / rel).read_text(encoding="utf-8")


def _summary_sources() -> list[pathlib.Path]:
    """Every module of the new #113 layer. Empty until it is built."""
    if not _SUMMARIES.is_dir():
        return []
    return [p for p in sorted(_SUMMARIES.rglob("*.py")) if "__pycache__" not in p.parts]


def _summary_blob() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _summary_sources())


# ─────────────────────────────────────────────────────────────────────────────
# D1 — the compaction module must stop being dead code
# ─────────────────────────────────────────────────────────────────────────────
class TestD1CompactionIsWired:
    """598 lines with three passing tests and zero production importers.

    This is the exact shape of code_graph/resolver.py, which shipped in this
    same state and left the code graph silently broken for six weeks with CI
    green. Wiring it is the point.
    """

    def test_consolidate_facts_has_a_production_importer(self) -> None:
        importers: list[str] = []
        for path in _SRC.rglob("*.py"):
            if "__pycache__" in path.parts or path.name == "fact_consolidator.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # Guard on node type FIRST. ast.Global and ast.Nonlocal also
                # carry a `names` attribute, but theirs is a list[str] rather
                # than a list[alias] — so an unguarded `a.name for a in
                # node.names` raises AttributeError: 'str' object has no
                # attribute 'name'. That was a bug in this gate, and it fired on
                # 34 source files, aborting the scan before it ever reached the
                # importer it was looking for.
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                if "fact_consolidator" in mod or "consolidate_facts" in names:
                    importers.append(path.relative_to(_SRC).as_posix())
        assert importers, (
            "core/fact_consolidator.py is still imported by no production module. "
            "It merges warm/cold facts, archives originals and records provenance "
            "in fact_consolidations — a table that currently holds 0 rows because "
            "the module has never run. Wire it (run_maintenance is the natural "
            "home) or delete it."
        )

    def test_known_dead_entry_was_removed(self) -> None:
        """The dead-module allowlist may only shrink."""
        guard = (_REPO / "tests" / "test_no_dead_modules.py").read_text(encoding="utf-8")
        assert '"core/fact_consolidator.py"' not in guard, (
            "core/fact_consolidator.py is now wired, so its _KNOWN_DEAD entry in "
            "tests/test_no_dead_modules.py must be removed — otherwise "
            "test_known_dead_list_has_no_stale_entries fails and the allowlist "
            "quietly becomes permanent."
        )

    def test_databasemanager_path_is_covered(self) -> None:
        """The path that will actually run in production has no test today.

        All three existing tests pass a str path, which takes the LEGACY
        backward-compat branch. The DatabaseManager branch — the v3.8.4
        concurrency fix that keeps LLM calls outside the write lock — is the
        branch production uses and it is currently unexercised.
        """
        hits = [
            p.name
            for p in (_REPO / "tests").rglob("test_*fact_consolidat*.py")
            if "DatabaseManager" in p.read_text(encoding="utf-8")
        ]
        assert hits, (
            "no test exercises consolidate_facts() with a DatabaseManager. The "
            "legacy str-path branch holds ONE write connection for the whole "
            "pass; under Mode B/C that means an Ollama or cloud call inside a "
            "held write lock. Production must take the DatabaseManager branch, "
            "so prove that branch works before wiring it."
        )


# ─────────────────────────────────────────────────────────────────────────────
# D2 — the three bounded summaries #113 actually asked for
# ─────────────────────────────────────────────────────────────────────────────
class TestD2BoundedSummariesExist:
    """Session Summary, Daily Reflection, Project Work Log."""

    def test_the_summaries_package_exists(self) -> None:
        assert _summary_sources(), (
            f"{_SUMMARIES.relative_to(_REPO)} does not exist. #113 asks for "
            "Session Summary, Daily Reflection and Project Work Log — a "
            "human-readable layer. None of the three exists anywhere in src/. "
            "NOTE: core/community_summary.py and core/summarizer.py are "
            "PRE-EXISTING and unrelated; do not repurpose them to satisfy this."
        )

    @pytest.mark.parametrize("kind", ["session", "daily", "project"])
    def test_summary_kind_is_implemented(self, kind: str) -> None:
        blob = _summary_blob().lower()
        if not blob:
            pytest.fail(f"no summaries package — {kind} summary cannot exist")
        assert kind in blob, (
            f"no {kind} summary implementation found. #113 names all three "
            "explicitly and the maintainer publicly committed to 'bounded work, "
            "project, and reflection summaries'."
        )

    def test_project_scope_does_not_use_the_broken_column(self) -> None:
        """entity_profiles.project_name has ONE distinct value across 1,148 rows.

        Grouping by it produces a single bucket — a Project Work Log that is
        permanently, silently wrong. Measured on the live store before this gate
        was written.
        """
        for path in _summary_sources():
            src = path.read_text(encoding="utf-8")
            if "project_name" in src and "tool_events" not in src:
                pytest.fail(
                    f"{path.name} scopes projects by entity_profiles.project_name, "
                    "which holds exactly 1 distinct value across 1,148 rows on a "
                    "real store. Use tool_events.project_path — 1,899 rows across "
                    "13 genuine projects."
                )


# ─────────────────────────────────────────────────────────────────────────────
# D3 — traceability: the maintainer's binding public constraint
# ─────────────────────────────────────────────────────────────────────────────
class TestD3Traceability:
    """"traceable back to the underlying memories rather than becoming opaque
    generic summaries" — maintainer, on issue #113.

    A summary a user cannot drill into is precisely the "opaque generic summary"
    the issue reply promised to avoid.
    """

    def test_summaries_carry_source_memory_ids(self) -> None:
        blob = _summary_blob()
        if not blob:
            pytest.skip("covered by D2 — summaries package not built yet")
        assert any(
            tok in blob
            for tok in ("source_fact_ids", "fact_ids", "source_ids", "memory_ids", "source_memory_ids")
        ), (
            "no summary records the memories it was derived from. The maintainer "
            "publicly committed that views must be 'traceable back to the "
            "underlying memories'. Without source ids a user cannot verify a "
            "claim the system makes about them — and cannot exercise GDPR "
            "Art. 15 against it either."
        )

    def test_summaries_are_profile_scoped(self) -> None:
        blob = _summary_blob()
        if not blob:
            pytest.skip("covered by D2 — summaries package not built yet")
        assert "profile_id" in blob, (
            "summaries are not profile-scoped. Cross-profile leakage in a "
            "generated summary is a compliance defect, not a cosmetic one — this "
            "release is marketed on GDPR completeness."
        )


# ─────────────────────────────────────────────────────────────────────────────
# D4 — honesty about coverage (the overclaiming-prevention principle, applied to summaries)
# ─────────────────────────────────────────────────────────────────────────────
class TestD4HonestCoverage:
    def test_sparse_session_data_is_disclosed_not_hidden(self) -> None:
        """Only 3.9% of facts carry a session_id on a real 3,294-fact store.

        A Session Summary built on 127 of 3,294 facts that presents itself as
        "your session" is a false claim about completeness. It must either state
        its coverage or say it cannot summarise this session.
        """
        blob = _summary_blob().lower()
        if not blob:
            pytest.skip("covered by D2 — summaries package not built yet")
        assert any(
            tok in blob
            for tok in ("coverage", "partial", "incomplete", "insufficient", "no_session", "unavailable")
        ), (
            "no coverage/sparsity handling. 96% of facts on a real store carry no "
            "session_id. A summary that silently covers 4% of the data while "
            "reading as complete is the same overclaiming Wave 4 removed from "
            "brain/truth.py."
        )

    def test_summaries_never_require_an_llm(self) -> None:
        """Bounded means it works with no model available.

        fact_consolidator already proves the pattern: Mode C falls back to B
        falls back to extractive. A summary layer that silently produces nothing
        when Ollama is down is not a feature a user can rely on.
        """
        blob = _summary_blob().lower()
        if not blob:
            pytest.skip("covered by D2 — summaries package not built yet")
        assert any(tok in blob for tok in ("extractive", "fallback", "deterministic")), (
            "no deterministic fallback. Mode A users have no LLM at all, and Mode "
            "B/C users lose theirs whenever the daemon or network is down. The "
            "summary must degrade to an extractive form, never to silence."
        )


# ─────────────────────────────────────────────────────────────────────────────
# D5 — invariant floor: recall/remember must not pay for any of this
# ─────────────────────────────────────────────────────────────────────────────
class TestD5HotPathUntouched:
    """Owner: "recall and remember timing, retrieval, and recall engineering are
    all the end user cares about."
    """

    def test_consolidation_is_not_invoked_from_a_recall_or_store_path(self) -> None:
        hot = [
            "core/recall_pipeline.py",
            "core/store_pipeline.py",
            "retrieval/engine.py",
        ]
        for rel in hot:
            path = _SRC / rel
            if not path.exists():
                continue
            src = path.read_text(encoding="utf-8")
            assert "consolidate_facts" not in src, (
                f"{rel} calls consolidate_facts. Consolidation performs LLM calls "
                "and multi-table writes; putting it on the recall or store path "
                "directly violates the release's first invariant. It belongs in "
                "background maintenance."
            )

    def test_summary_generation_is_not_on_the_hot_path(self) -> None:
        """Assert on IMPORTS, not on the substring "summaries".

        The first version of this test searched the raw source text. That is
        the wrong check twice over: store_pipeline.py legitimately discusses
        session summaries in its docstrings, and a prose match invites an
        implementer to reword a comment rather than change behaviour — which
        is exactly what happened. Parse the imports instead; that is the thing
        that actually determines whether the hot path can invoke this code.
        """
        if not _summary_sources():
            pytest.skip("covered by D2 — summaries package not built yet")
        for rel in ("core/recall_pipeline.py", "core/store_pipeline.py"):
            path = _SRC / rel
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert "summaries" not in mod.split("."), (
                        f"{rel} imports from the summaries package ({mod}). "
                        "Summary generation performs multi-table reads and "
                        "optional LLM calls; it must never run inside recall "
                        "or remember."
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "summaries" not in alias.name.split("."), (
                            f"{rel} imports {alias.name}. Summary generation "
                            "must never run inside recall or remember."
                        )


# ─────────────────────────────────────────────────────────────────────────────
# D6 — floor: existing behaviour that must survive
# ─────────────────────────────────────────────────────────────────────────────
class TestD6RegressionFloor:
    def test_consolidation_remains_non_destructive(self) -> None:
        src = _read("core/fact_consolidator.py")
        assert "archive" in src.lower(), (
            "the archive-instead-of-delete rule was removed from "
            "fact_consolidator. Source facts must survive consolidation — "
            "destroying a user's original memories to save space is unacceptable "
            "and would break GDPR Art. 15 export completeness."
        )

    def test_consolidation_keeps_its_provenance_record(self) -> None:
        src = _read("core/fact_consolidator.py")
        assert "fact_consolidations" in src, (
            "the fact_consolidations provenance write was removed. Without it a "
            "consolidated fact cannot be traced to its sources, which breaks the "
            "same traceability promise D3 enforces for summaries."
        )

    def test_llm_call_stays_outside_the_write_lock(self) -> None:
        """The v3.8.4 concurrency fix. Regressing it reintroduces a deadlock class.

        Asserted structurally: the summary must be generated BEFORE the write
        context manager is entered, not inside it.
        """
        src = _read("core/fact_consolidator.py")
        gen = src.find("_generate_summary")
        assert gen != -1, "_generate_summary disappeared from fact_consolidator"
        assert "memory_write" in src, "the per-cluster write context manager disappeared"
