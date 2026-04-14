# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Skill Evolution type definitions.

Immutable data classes for evolution candidates, records, and lineage.
All types are frozen dataclasses — no mutation after creation.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EvolutionType(str, Enum):
    """How the skill is being evolved."""
    FIX = "fix"           # Repair broken skill in-place
    DERIVED = "derived"   # Create specialized variant
    CAPTURED = "captured" # Extract new skill from patterns


class TriggerType(str, Enum):
    """What triggered the evolution."""
    POST_SESSION = "post_session"   # Session Stop hook analysis
    DEGRADATION = "degradation"     # Behavioral assertion confidence drop
    HEALTH_CHECK = "health_check"   # Periodic consolidation scan


class EvolutionStatus(str, Enum):
    """Pipeline status."""
    CANDIDATE = "candidate"     # Detected, not yet confirmed
    CONFIRMED = "confirmed"     # LLM gate passed
    MUTATED = "mutated"         # New SKILL.md generated
    VERIFIED = "verified"       # Blind verification passed
    PROMOTED = "promoted"       # Live — evolved skill active
    REJECTED = "rejected"       # Failed verification or gate
    FAILED = "failed"           # Error during evolution


@dataclass(frozen=True)
class EvolutionCandidate:
    """A skill flagged for potential evolution."""
    skill_name: str
    evolution_type: EvolutionType
    trigger: TriggerType
    evidence: tuple[str, ...] = ()
    effective_score: float = 0.0
    invocation_count: int = 0
    session_id: str = ""
    project_path: str = ""


@dataclass(frozen=True)
class EvolutionRecord:
    """Persisted record of an evolution attempt."""
    id: str
    skill_name: str
    parent_skill_id: Optional[str]
    evolution_type: EvolutionType
    trigger: TriggerType
    generation: int = 0
    status: EvolutionStatus = EvolutionStatus.CANDIDATE
    mutation_summary: str = ""
    evidence: tuple[str, ...] = ()
    original_content: str = ""
    evolved_content: str = ""
    content_diff: str = ""
    blind_verified: bool = False
    rejection_reason: str = ""
    created_at: str = ""
    completed_at: str = ""


@dataclass(frozen=True)
class SkillLineage:
    """Lineage metadata for an evolved skill."""
    skill_id: str
    parent_skill_id: Optional[str]
    evolution_type: EvolutionType
    generation: int
    trigger: TriggerType
    mutation_summary: str = ""
    created_at: str = ""

    @property
    def is_root(self) -> bool:
        return self.parent_skill_id is None
