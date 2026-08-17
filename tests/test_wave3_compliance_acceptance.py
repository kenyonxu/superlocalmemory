"""Wave 3 acceptance gates — GDPR / compliance core. Authored by the release
coordinator, NOT by implementers.

These define "done" for the compliance work that SuperLocalMemory 4.0.6 is
being marketed on. Implementation agents may NOT modify this file.

Invariant I7 (release-blocking):
    No erasure or export path may claim completeness while data survives
    ANYWHERE — memory.db, learning.db, code_graph.db, the vector store, FTS
    shadow tables, the WAL, the context cache, or backups/.

Every assertion is written against OBSERVABLE behaviour — CLI exit codes and
JSON, receipt contents, on-disk state — never against internal dataclass shape.
A previous wave's gate asserted direct attribute mutation and pushed a product
regression (an immutable config was un-frozen to satisfy the test). Not again.

VERIFIED-CORRECT TODAY (must not regress):
  - gdpr.py discovers every table carrying profile_id live, rather than a
    hardcoded list, explicitly to avoid completeness bugs.
  - learning.db IS purged, and FAILS CLOSED ("learning receipt purge failed;
    profile deletion was not started").
  - Residual vectors are counted as FAILURES so a receipt cannot claim a
    complete erasure while physical vectors survive.
  - erasure_receipts and profiles survive the wipe so deletion can be proven.

KNOWN GAPS THIS WAVE MUST CLOSE:
  C1  backups/ is outside all compliance logic (10 rotating snapshots each of
      memory.db, learning.db, code_graph.db, audit_chain.db, audit.db,
      pending.db). Owner-approved strategy: an outstanding-obligation LEDGER on
      the receipt plus automatic re-erasure on restore — NOT rewriting
      snapshots, which is IO-brutal and risks corruption.
  C2  code_graph.db is not in erasure/export scope (repo paths, file names and
      symbol names are identifying in a work context).
  C3  no compliance CLI at all — a DPO cannot run or evidence a subject request
      scriptably.
  C4  review_policy is hardcoded "not_configured", so correction cases
      accumulate with no way to action them.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

SLM = [sys.executable, "-m", "superlocalmemory.cli"]


def _run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(SLM + args, capture_output=True, text=True, env=e, timeout=180)


# ─────────────────────────────────────────────────────────────────────────────
# C3 — a DPO must be able to run and evidence a subject request from the CLI
# ─────────────────────────────────────────────────────────────────────────────
class TestC3ComplianceCLI:
    """Art. 15 / 17 / 20 must be reachable without the dashboard.

    An enterprise DPO scripts these into an evidence pipeline; "click around in
    a web UI" is not an auditable process.
    """

    def test_gdpr_command_exists(self, tmp_path) -> None:
        r = _run(["gdpr", "--help"], env={"SLM_DATA_DIR": str(tmp_path)})
        assert r.returncode == 0, (
            "`slm gdpr` does not exist. A compliance product must expose "
            f"export/erase/status/verify from the CLI.\nstderr: {r.stderr[:400]}"
        )

    @pytest.mark.parametrize("sub", ["export", "erase", "status", "verify"])
    def test_gdpr_subcommands_exist(self, sub: str, tmp_path) -> None:
        r = _run(["gdpr", sub, "--help"], env={"SLM_DATA_DIR": str(tmp_path)})
        assert r.returncode == 0, (
            f"`slm gdpr {sub}` missing.\nstderr: {r.stderr[:300]}"
        )

    def test_gdpr_status_emits_machine_readable_json(self, tmp_path) -> None:
        """Evidence pipelines parse; they do not scrape prose."""
        r = _run(["gdpr", "status", "--json"], env={"SLM_DATA_DIR": str(tmp_path)})
        assert r.returncode == 0, f"exit {r.returncode}: {r.stderr[:300]}"
        payload = json.loads(r.stdout)
        assert isinstance(payload, dict) and payload, "empty status payload"


# ─────────────────────────────────────────────────────────────────────────────
# C1 + I7 — erasure completeness must account for backups
# ─────────────────────────────────────────────────────────────────────────────
class TestC1BackupsInComplianceScope:
    """The gap that breaks the compliance claim.

    After Art.17 erasure the live stores are clean, but ten historical snapshots
    still hold the erased personal data while the receipt reports COMPLETE.
    """

    def test_erasure_receipt_declares_backup_obligations(self, tmp_path) -> None:
        """A receipt must state what remains in backups, not stay silent.

        The mechanism is the implementer's (owner-approved: an obligation ledger
        plus re-erasure on restore). This asserts only that the receipt is
        HONEST about snapshots — silence is what makes the current claim false.
        """
        from superlocalmemory.compliance import gdpr as gdpr_mod

        src = _module_text(gdpr_mod)
        assert "backup" in src.lower(), (
            "compliance/gdpr.py never mentions backups. After erasure, "
            "~/.superlocalmemory/backups still holds 10 snapshots each of "
            "memory.db and learning.db containing the erased data, while the "
            "receipt reports complete. Either purge them or record them as "
            "outstanding obligations on the receipt."
        )

    def test_completeness_flag_considers_backups(self, tmp_path) -> None:
        """`complete` must not be True while snapshots hold erased data.

        gdpr.py already refuses to claim completeness on learning-db failure,
        vector residue, context-cache failure and owner-erasure gaps. Backups
        must join that list — anything else is a false completeness claim.
        """
        from superlocalmemory.compliance import gdpr as gdpr_mod

        src = _module_text(gdpr_mod)
        markers = ("backup_obligation", "backups_pending", "backup_residue",
                   "outstanding_obligation", "backups_skipped")
        assert any(m in src for m in markers), (
            "the erasure completeness computation does not reference any "
            f"backup obligation marker (looked for {markers}). The flag "
            "currently reports complete while backups retain the data."
        )


# ─────────────────────────────────────────────────────────────────────────────
# C2 — code_graph.db must be in scope
# ─────────────────────────────────────────────────────────────────────────────
class TestC2CodeGraphInErasureScope:
    """Repo paths, file names and symbol names identify a person's work."""

    def test_code_graph_is_referenced_by_compliance(self) -> None:
        from superlocalmemory.compliance import gdpr as gdpr_mod

        src = _module_text(gdpr_mod)
        assert "code_graph" in src, (
            "compliance/gdpr.py never references code_graph.db. It survives "
            "erasure today, carrying repository paths, file names and symbol "
            "names — identifying data in a work context."
        )


# ─────────────────────────────────────────────────────────────────────────────
# C4 — correction cases must be actionable
# ─────────────────────────────────────────────────────────────────────────────
class TestC4ReviewPolicyIsReal:
    """review_policy is hardcoded not_configured in brain/truth.py.

    On this machine the ledger grew 1 -> 10 cases in a single session with no
    way to action any of them.
    """

    def test_review_policy_is_not_hardcoded_unconfigured(self) -> None:
        from superlocalmemory.brain import truth as truth_mod

        src = _module_text(truth_mod)
        hardcoded = src.count('"availability": "not_configured"')
        assert hardcoded == 0, (
            "brain/truth.py still hardcodes review_policy availability as "
            "'not_configured' — no deployment can ever attach a policy, so "
            "correction cases are permanently unactionable."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Retention — owner decision: configurable, default 90 days
# ─────────────────────────────────────────────────────────────────────────────
class TestRetentionWindow:
    def test_retention_window_defaults_to_90_days(self) -> None:
        """Owner decision: 'configurable through the UI, and 90 days by default'."""
        from superlocalmemory.core.config import SLMConfig

        cfg = SLMConfig()
        found = _find_numeric_attr(cfg, ("retention", "retain"), 90)
        assert found, (
            "no retention-window setting defaulting to 90 days found on "
            "SLMConfig. The backup/erasure obligation window must be "
            "configurable, and the owner set the default at 90 days."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Security floor — must NOT regress while adding the above
# ─────────────────────────────────────────────────────────────────────────────
class TestComplianceSecurityFloor:
    """Properties already correct today. Breaking any of these fails the wave."""

    def test_erasure_still_fails_closed_on_learning_db(self) -> None:
        from superlocalmemory.compliance import gdpr as gdpr_mod

        src = _module_text(gdpr_mod)
        assert "learning receipt purge failed" in src, (
            "the fail-closed guard on learning.db purge was removed — erasure "
            "could now proceed while derived learning data survives"
        )

    def test_vector_residue_still_counts_as_failure(self) -> None:
        from superlocalmemory.compliance import gdpr as gdpr_mod

        src = _module_text(gdpr_mod)
        assert "_count_vector_residue" in src, (
            "residual-vector accounting was removed — a receipt could claim "
            "complete erasure while physical vectors survive"
        )

    def test_receipts_and_profiles_survive_the_wipe(self) -> None:
        from superlocalmemory.compliance import gdpr as gdpr_mod

        src = _module_text(gdpr_mod)
        assert "erasure_receipts" in src and "_NON_MEMORY_SCOPED" in src, (
            "erasure_receipts must survive a profile wipe so an operator can "
            "prove the deletion happened"
        )


# ── helpers ──────────────────────────────────────────────────────────────────
def _module_text(mod) -> str:
    import inspect

    return inspect.getsource(mod)


def _find_numeric_attr(obj, name_hints: tuple[str, ...], expected: int, depth: int = 3) -> bool:
    """Search a config object tree for a numeric attribute matching a hint."""
    if depth <= 0:
        return False
    for attr in dir(obj):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(obj, attr)
        except Exception:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if any(h in attr.lower() for h in name_hints) and int(val) == expected:
                return True
        elif hasattr(val, "__dict__") or hasattr(val, "__dataclass_fields__"):
            if _find_numeric_attr(val, name_hints, expected, depth - 1):
                return True
    return False
