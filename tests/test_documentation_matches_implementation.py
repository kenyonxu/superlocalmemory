"""Documentation audit gates — documentation must not outrun implementation.
Authored by the release coordinator, NOT by implementers.

OWNER CONTEXT (verbatim): "the main thing about this V4 version is product GDPR
policies. That is the main core of this product... we are marketing it as the
full compliance product for the company's teams and everyone."

That is precisely why this gate exists. A compliance product that overstates its
own compliance is the worst possible failure mode: the claim is the product, so
an inflated claim is a defect in the product itself, not a marketing nit.

THE RULE: no user-facing document may assert a capability the code does not
implement, and no document may imply certification SLM does not hold.

WHAT IS ALREADY RIGHT (measured before writing this — must not regress):
  README.md:555  "provides local storage, export/erasure commands, provenance,
                  policy, and audit features that CAN SUPPORT a compliance
                  program"
  README.md:605  "ships built-in controls that SUPPORT GDPR compliance programs"
Those are the correct register — capability, not certification. v3.4 removed
"EU AI Act compliant" from the MCP Mode descriptions for the same reason, and
tests/test_mcp/test_f02_mode_no_compliance_claims.py guards that. This file
extends that discipline to the docs.

THE OPEN TENSION THIS WAVE MUST RESOLVE:
  README.md:44 renders a badge "Enterprise-GDPR | EU AI Act". A badge is read as
  certification by exactly the non-technical audience this release targets, even
  when the body text three hundred lines below is properly hedged. Either the
  badge states a posture rather than a standard, or the body's hedge is elevated
  to sit with it.

SCOPE NOTE: this file checks DOCUMENTS against FACTS THE TEST SUITE CAN ESTABLISH.
It cannot judge prose quality. Independent review covers that, and the
owner reviews the final wording.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "superlocalmemory"

#: User-facing documents. Internal design notes are deliberately excluded — they
#: are allowed to discuss unshipped ideas; shipped docs are not.
_USER_DOCS = [
    "README.md",
    "SECURITY.md",
    "docs/compliance.md",
    "docs/deployment-tiers.md",
    "docs/rbac-teams.md",
    "docs/cli-reference.md",
]


def _docs() -> dict[str, str]:
    out = {}
    for rel in _USER_DOCS:
        p = _REPO / rel
        if p.exists():
            out[rel] = p.read_text(encoding="utf-8", errors="ignore")
    return out


def _released_version() -> str:
    """The version this release actually ships, from the packaging metadata."""
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert m, "could not read version from pyproject.toml"
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────────────
# D1 — no certification claims
# ─────────────────────────────────────────────────────────────────────────────
class TestNoCertificationClaims:
    """SLM is not certified against anything. It must never imply otherwise."""

    #: Phrases that assert conformance rather than capability.
    _FORBIDDEN = [
        r"\bGDPR[- ]compliant\b",
        r"\bEU AI Act[- ]compliant\b",
        r"\bfully compliant\b",
        r"\bis compliant with\b",
        r"\bcertified\b",
        r"\bguarantees compliance\b",
        r"\bHIPAA[- ]compliant\b",
        r"\bSOC ?2 (certified|compliant)\b",
        r"\bISO ?27001 (certified|compliant)\b",
    ]

    @pytest.mark.parametrize("pattern", _FORBIDDEN)
    def test_no_conformance_assertion(self, pattern: str) -> None:
        offenders = []
        for rel, text in _docs().items():
            for m in re.finditer(pattern, text, re.I):
                line = text[: m.start()].count("\n") + 1
                offenders.append(f"{rel}:{line} — {m.group(0)!r}")
        assert not offenders, (
            "documents assert CONFORMANCE rather than CAPABILITY:\n  "
            + "\n  ".join(offenders)
            + "\n\nSLM holds no certification. It provides controls that help a "
            "team RUN a compliance programme. The README already words this "
            'correctly ("features that can support a compliance program") — '
            "match that register everywhere."
        )

    def test_badges_do_not_imply_certification(self) -> None:
        """A badge is read as a seal of approval, regardless of nearby prose."""
        readme = (_REPO / "README.md").read_text(encoding="utf-8", errors="ignore")
        badges = re.findall(r"img\.shields\.io[^\)\s]*", readme)
        suspect = [
            b for b in badges
            if re.search(r"GDPR|EU_?AI_?Act|HIPAA|SOC ?2|ISO", b, re.I)
            and not re.search(r"ready|posture|controls|support|toolkit", b, re.I)
        ]
        assert not suspect, (
            "these badges name a regulation without qualifying the relationship:\n  "
            + "\n  ".join(suspect)
            + "\n\nA badge reading 'GDPR | EU AI Act' is read as certification by "
            "the non-technical audience this release targets — 75% of users. "
            "Qualify it (e.g. '…-controls' or '…-ready') so the badge says what "
            "the body text says."
        )


# ─────────────────────────────────────────────────────────────────────────────
# D2 — version consistency
# ─────────────────────────────────────────────────────────────────────────────
class TestVersionConsistency:
    def test_no_stale_previous_version_in_user_docs(self) -> None:
        """A release that still advertises the previous version is not shipped.

        WIDENED after the first pass missed two live references. The original
        check only looked at ``<code>vX.Y.Z</code>`` spans, so it went green
        while README.md's own <h1> still read "SuperLocalMemory V4.0.5" and the
        "Current Release" shields badge still said v4.0.5. Those are the two
        most prominent version statements on the page — a reader sees them
        before any prose. Check every "this IS the release" position.
        """
        version = _released_version()
        stale = []
        #: Patterns that assert "this is the current release". Historical prose
        #: ("What V4.0.5 shipped", migration notes) is legitimately about an old
        #: release and is deliberately NOT matched here.
        current_release_positions = [
            r"<code>v?(\d+\.\d+\.\d+)</code>",          # headline code span
            r"<h1[^>]*>[^<]*?V(\d+\.\d+\.\d+)[^<]*</h1>",  # document title
            r"badge/v?(\d+\.\d+\.\d+)-Current_Release",  # release badge label
            r'alt="v?(\d+\.\d+\.\d+) — Current Release"',  # its alt text
        ]
        for rel, text in _docs().items():
            for pattern in current_release_positions:
                for m in re.finditer(pattern, text, re.I):
                    if m.group(1) != version:
                        line = text[: m.start()].count("\n") + 1
                        stale.append(
                            f"{rel}:{line} — advertises v{m.group(1)} as current"
                        )
        assert not stale, (
            f"pyproject.toml ships {version}, but user-facing docs still "
            "advertise a different release as current:\n  " + "\n  ".join(stale)
        )

    def test_changelog_documents_this_release(self) -> None:
        version = _released_version()
        changelog = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8", errors="ignore")
        assert version in changelog, (
            f"CHANGELOG.md has no entry for {version}. Every shipped release "
            "needs a changelog entry describing what changed for the user."
        )


# ─────────────────────────────────────────────────────────────────────────────
# D3 — documented commands must exist
# ─────────────────────────────────────────────────────────────────────────────
class TestDocumentedCommandsExist:
    """A CLI reference that lists commands the binary lacks is a broken promise."""

    def test_documented_gdpr_subcommands_are_implemented(self) -> None:
        docs = _docs()
        blob = "\n".join(docs.values())
        claimed = set(re.findall(r"slm gdpr (\w+)", blob))
        if not claimed:
            pytest.skip("no `slm gdpr` subcommands documented")
        cli = (_SRC / "cli" / "gdpr_cmd.py")
        assert cli.exists(), "docs reference `slm gdpr` but cli/gdpr_cmd.py is absent"
        impl = cli.read_text(encoding="utf-8")
        missing = sorted(c for c in claimed if c not in impl)
        assert not missing, (
            f"documented but not implemented: {missing}. A DPO following the "
            "docs would hit a missing command mid-audit."
        )


# ─────────────────────────────────────────────────────────────────────────────
# D4 — floor: guarantees already enforced elsewhere must stay enforced
# ─────────────────────────────────────────────────────────────────────────────
class TestComplianceGuardsStillActive:
    def test_mode_descriptions_still_free_of_compliance_claims(self) -> None:
        """v3.4 removed "EU AI Act compliant" from MCP Mode descriptions."""
        tools = (_SRC / "mcp" / "tools_v3.py").read_text(encoding="utf-8")
        assert "EU AI Act" not in tools, (
            "an EU AI Act claim reappeared in the MCP Mode descriptions. Wave 3 "
            "removed it; test_f02_mode_no_compliance_claims.py guards it. Mode "
            "descriptions state CAPABILITY (Local Guardian / Smart Local / Full "
            "Power), never regulatory conformance."
        )

    def test_erasure_completeness_guards_still_present(self) -> None:
        """Docs describe fail-closed erasure; the code must still fail closed."""
        gdpr = (_SRC / "compliance" / "gdpr.py").read_text(encoding="utf-8")
        for marker in ("backup_obligations_pending", "fts_residue", "_count_vector_residue"):
            assert marker in gdpr, (
                f"{marker} disappeared from compliance/gdpr.py. The docs describe "
                "fail-closed cross-store erasure; removing a completeness guard "
                "turns that documented guarantee into an overclaim."
            )
