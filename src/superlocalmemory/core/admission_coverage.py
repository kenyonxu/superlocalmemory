# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Startup check that every MCP tool and operation kind has an admission policy.

Moved out of ``core/admission.py`` in 4.1.25 to keep that module within the
size budget; ``admission`` re-exports :func:`coverage_self_check`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from superlocalmemory.core.operation_policy_registry import _DEFAULT_REGISTRY
from superlocalmemory.core.operation_request import OperationKind

if TYPE_CHECKING:
    from superlocalmemory.core.config import DeploymentConfig
    from superlocalmemory.core.operation_policy_registry import OperationPolicyRegistry

logger = logging.getLogger("superlocalmemory.core.admission")



def coverage_self_check(
    deployment: "DeploymentConfig",
    registry: "OperationPolicyRegistry | None" = None,
    server: "object | None" = None,
) -> None:
    """Assert comprehensive policy coverage at daemon startup.

    Checks:
      1. Every OperationKind has a registered policy (existing check).
      2. No policy has an empty allowed_transports set (unreachable = bug).
      3. Every tool in _REQUIRED_MCP_GATES appears in _GATED_MCP_TOOLS.
      4. (F1) Dynamic: every mutating tool in server._tool_manager._tools
         that is NOT flagged readOnlyHint=True must be in _GATED_MCP_TOOLS.

    In personal/local mode: logs warnings for any gap (non-fatal).
    In enterprise mode: raises RuntimeError on the first gap (fatal startup).

    Parameters
    ----------
    deployment : Loaded DeploymentConfig (from unified_daemon startup).
    registry   : Override the default registry (for testing).
    server     : Optional FastMCP server; when supplied, its tool registry
                 is enumerated for check 4 (dynamic mutator coverage).
    """
    # Imported here: admission re-exports this function at its end.
    from superlocalmemory.core.admission import (
        _GATED_MCP_TOOLS,
        _REQUIRED_MCP_GATES,
        _tool_read_only_hint,
    )

    reg = registry if registry is not None else _DEFAULT_REGISTRY
    is_enterprise = deployment.is_enterprise
    messages: list[str] = []

    # Check 1: every OperationKind has a policy entry.
    cov = reg.coverage()
    missing_kinds = [
        kind.value
        for kind in OperationKind
        if not cov.get(kind.value, {}).get("has_policy", False)
    ]
    if missing_kinds:
        messages.append(f"policy coverage gap — no policy for: {missing_kinds}")

    # Check 2: no policy has empty allowed_transports (unreachable).
    empty_transport_kinds = [
        info["kind"]
        for info in cov.values()
        if info.get("has_policy") and not info.get("has_transports", True)
    ]
    if empty_transport_kinds:
        messages.append(
            f"empty_transports in policies for: {empty_transport_kinds} "
            "(no reachable transport — these operations can never be invoked)"
        )

    # Check 3: tool inventory — every required MCP gate is wired.
    ungated = sorted(_REQUIRED_MCP_GATES - _GATED_MCP_TOOLS)
    if ungated:
        messages.append(f"ungated MCP tools (missing @admits): {ungated}")

    # Check 4 (F1): dynamic discovery — enumerate server tool registry and flag
    # any mutating tool (readOnlyHint / read_only_hint != True) not in
    # _GATED_MCP_TOOLS. See _tool_read_only_hint for mcp 2.0 naming.
    if server is not None:
        try:
            tool_dict = server._tool_manager._tools  # type: ignore[attr-defined]
            dynamic_ungated = sorted(
                name
                for name, tool in tool_dict.items()
                if name not in _GATED_MCP_TOOLS
                and _tool_read_only_hint(tool) is not True
            )
            if dynamic_ungated:
                messages.append(
                    f"dynamic ungated MCP mutators (not in _GATED_MCP_TOOLS): {dynamic_ungated}"
                )
        except AttributeError:
            logger.debug(
                "admission: server does not expose _tool_manager._tools — skipping dynamic check"
            )

    if not messages:
        logger.debug("admission: coverage self-check passed (%d kinds)", len(list(OperationKind)))
        return

    for msg in messages:
        full = f"admission: {msg}"
        if is_enterprise:
            raise RuntimeError(full)
        logger.warning(full)


__all__ = ["coverage_self_check"]
