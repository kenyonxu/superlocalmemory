# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory issue #113 — Personal Memory Views.

Three bounded, profile-scoped, traceable summaries:

  Session Summary    — what happened in a specific session (data is sparse:
                       only ~3.9% of facts carry a session_id on a real store;
                       coverage is always disclosed explicitly).
  Daily Reflection   — what was recorded on a specific date.
  Project Work Log   — what tool events and facts belong to a project,
                       scoped by tool_events.project_path (NOT by
                       entity_profiles.project_name, which has one distinct
                       value across 1,148 rows and is useless for scoping).

Every SummaryResult carries:
  - source_fact_ids  for traceability (maintainer's binding constraint)
  - profile_id       — cross-profile access is not permitted
  - coverage         — honest assessment; never silently partial

Deferred to 4.0.7: prompt-driven custom views.  They are non-deterministic,
hard to make traceable, and a prompt-injection surface.
"""

from .base import SummaryResult
from .daily_reflection import generate_daily_reflection
from .project_work_log import generate_project_work_log
from .session_summary import generate_session_summary

__all__ = [
    "SummaryResult",
    "generate_session_summary",
    "generate_daily_reflection",
    "generate_project_work_log",
]
