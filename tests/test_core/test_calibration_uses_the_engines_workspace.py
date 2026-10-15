# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Calibration reads the workspace from the engine, not from a config field.

``SLMConfig`` names the workspace ``active_profile``; calibration read
``config.profile_id``, which only the tests' stand-in config had. On a real
install ``slm embedder calibrate`` crashed with AttributeError right after an
embedding-model switch (found on the author's Mac, 13 Oct 2026).
"""

from __future__ import annotations

from types import SimpleNamespace

from superlocalmemory.core.calibration_control import CancelToken
from superlocalmemory.core.config import SLMConfig


def _engine(profile="work"):
    config = SLMConfig()
    assert not hasattr(config, "profile_id")  # the premise this test guards
    judge = object()
    return SimpleNamespace(profile_id=profile, _config=config,
                           _retrieval_engine=SimpleNamespace(_sufficiency_judge=judge))


def test_binding_works_with_the_real_config():
    token = CancelToken()
    token.bind(_engine())
    assert not token.is_set()


def test_switching_workspace_mid_calibration_cancels_it():
    engine = _engine("work")
    token = CancelToken()
    token.bind(engine)
    engine.profile_id = "personal"
    assert token.is_set()
