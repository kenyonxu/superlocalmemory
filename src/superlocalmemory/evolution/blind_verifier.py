# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Blind Verifier — information-isolated skill verification.

The key insight from EvoSkills (arXiv:2604.01687): when a generator
creates a skill and the same model verifies it, confirmation bias is
nearly guaranteed. The verifier must be BLIND to the generator's reasoning.

This verifier:
- Uses a DIFFERENT model from the generator (Haiku vs Sonnet)
- CANNOT see: original skill, mutation rationale, generator's reasoning
- CAN see: task description (what the skill should do), evolved SKILL.md
- Evaluates independently: "Does this skill correctly address the task?"

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationResult:
    """Result of blind verification."""

    passed: bool
    confidence: float  # 0.0-1.0
    issues: tuple[str, ...] = ()
    reasoning: str = ""


def build_verification_prompt(
    skill_name: str,
    skill_description: str,
    evolved_content: str,
) -> str:
    """Build blind verification prompt.

    The verifier sees ONLY:
    - What the skill is supposed to do (name + description)
    - The evolved skill content

    The verifier does NOT see:
    - The original skill
    - Why it was evolved
    - What evidence triggered evolution
    - The generator's reasoning
    """
    return f"""You are an independent skill quality reviewer. You have NOT seen the original
version of this skill or why it was modified. Evaluate it purely on its merits.

SKILL PURPOSE: {skill_name}
EXPECTED BEHAVIOR: {skill_description}

SKILL CONTENT TO REVIEW:
{evolved_content[:8000]}

EVALUATE:
1. Does the skill clearly explain what to do? (clarity)
2. Are the instructions specific and actionable? (specificity)
3. Are there any obvious errors, contradictions, or missing steps? (correctness)
4. Would an AI agent be able to follow these instructions? (executability)

RESPOND IN JSON FORMAT:
{{
  "passed": true/false,
  "confidence": 0.0-1.0,
  "issues": ["issue1", "issue2"],
  "reasoning": "brief explanation"
}}

Be strict. Only pass skills that are genuinely clear, correct, and actionable.
A mediocre skill that might work sometimes should FAIL — evolution should produce
clear improvements, not marginal changes."""


def _unique_fields(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("Duplicate verification field")
        data[key] = value
    return data


def parse_verification_response(response: str) -> VerificationResult:
    """Parse the verifier's JSON response."""
    try:
        # Decode the outer object, never an approving object nested inside it.
        start = re.search(r"[\[{]", response).start()
        end = max(response.rfind("}"), response.rfind("]")) + 1
        data = json.loads(response[start:end], object_pairs_hook=_unique_fields)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        confidence = data.get("confidence", 0.5)
        issues, reasoning = data.get("issues", []), data.get("reasoning", "")
        if type(confidence) not in (int, float):
            raise ValueError("Invalid confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("Confidence outside [0, 1]")
        if not isinstance(issues, list) or any(not isinstance(i, str) for i in issues):
            raise ValueError("Invalid issues")
        if not isinstance(reasoning, str):
            raise ValueError("Invalid reasoning")
        return VerificationResult(
            passed=data.get("passed") is True,
            confidence=float(confidence), issues=tuple(issues), reasoning=reasoning,
        )
    except (ValueError, TypeError, AttributeError):
        return VerificationResult(
            passed=False, confidence=0.3,
            reasoning="Could not parse verification response",
            issues=("Unparseable response",),
        )
