"""4.0.5 ranking policy: feedback adaptation is explicit, never implicit."""

from __future__ import annotations

from superlocalmemory.core.recall_pipeline import _resolve_ranking_mode


def test_ranking_defaults_to_observe_only() -> None:
    """An upgrade with no declared policy must not change recall from feedback."""
    assert _resolve_ranking_mode({}) == "off"


def test_explicit_operator_policy_can_enable_an_existing_ranking_mode() -> None:
    assert _resolve_ranking_mode({"SLM_RANKING": "v2-ensemble"}) == "v2-ensemble"


def test_legacy_toggle_does_not_reenable_adaptive_ranking_implicitly() -> None:
    assert _resolve_ranking_mode({"SLM_BANDIT_DISABLED": "1"}) == "off"
