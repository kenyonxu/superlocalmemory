# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Explicit manual export for local aggregate diagnostics."""

from __future__ import annotations

from argparse import Namespace


def cmd_diagnostics(args: Namespace) -> None:
    action = getattr(args, "diagnostics_command", None)
    if action == "reliability":
        _cmd_reliability(args)
        return
    if action != "export":
        raise SystemExit("choose a diagnostics subcommand: export, reliability")

    from superlocalmemory.cli.json_output import json_print
    from superlocalmemory.infra.local_diagnostics import default_diagnostics

    default_diagnostics().export_json(args.destination)
    data = {"exported": True, "reporting": "manual_export_only"}
    if bool(getattr(args, "json", False)):
        json_print("diagnostics", data=data)
        return
    print("Local aggregate diagnostics exported by explicit request.")
    print("No automatic reporting was enabled.")


def _cmd_reliability(args: Namespace) -> None:
    """Report whether wired mechanisms are effective, not merely present.

    ``implemented``, ``reachable`` and ``effective`` are three different
    questions. A grep answers the first and an import answers the second; only
    querying this store answers the third. Both checks are read-only.
    """
    from superlocalmemory.cli.json_output import json_print
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.reliability import (
        DEFAULT_MIN_OBSERVATIONS,
        check_beta_learners,
        check_schema_guards,
    )

    floor = getattr(args, "min_observations", None) or DEFAULT_MIN_OBSERVATIONS
    learners = check_beta_learners(state_path("learning.db"), min_observations=floor)
    guards = check_schema_guards(state_path("memory.db"))

    payload = {
        "learners": [
            {
                "table": v.table,
                "verdict": v.verdict,
                "units": v.units,
                "units_at_prior_mean": v.units_at_prior_mean,
                "units_matching_neutral_identity": v.units_matching_neutral_identity,
                "observations": v.observations,
                "detail": v.detail,
            }
            for v in learners
        ],
        "schema_guards": [
            {
                "name": g.name,
                "verdict": g.verdict,
                "missing": list(g.missing),
                "found_elsewhere": [
                    {
                        "table": tbl,
                        "column": col,
                        "populated_rows": n,
                        # -1 when coverage over the guarded table could not be
                        # measured. Populated rows alone overstate the remedy.
                        "coverage_pct_of_guarded_table": cov,
                    }
                    for tbl, col, n, cov in g.found_elsewhere
                ],
                "detail": g.detail,
            }
            for g in guards
        ],
    }

    if bool(getattr(args, "json", False)):
        json_print("diagnostics-reliability", data=payload)
        return

    if not learners and not guards:
        print("No Bayesian learners or schema-guarded paths found in this store.")
        return

    for v in learners:
        print(f"[{v.verdict}] {v.table}")
        print(f"    {v.detail}")
    for g in guards:
        print(f"[{g.verdict}] {g.name}")
        print(f"    {g.detail}")


__all__ = ["cmd_diagnostics"]
