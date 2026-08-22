# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""A mode with no model must say so, not just quietly do less.

Mode A runs with no language model at all — that is the point of it, and it is
why nothing on the store or recall path reaches the network. A summary in Mode A
is assembled from the user's own notes rather than written.

The surfaces already said *what* they did. None said *why*, or what to do about
it. Someone seeing a plainer summary than they expected had no way to learn that
a written one needs a model and which modes have one, and would reasonably
conclude the feature was broken.

These tests pin the explanation: present when there is no model, absent when
there is, and identical wherever it appears.
"""

from __future__ import annotations

import pytest

from superlocalmemory.core.config import Mode, SLMConfig
from superlocalmemory.core.mode_capability import llm_capability


class TestModeARefusesByConfigurationNotByAccident:
    """The promise should hold because the mode says so."""

    def test_mode_a_declares_that_it_will_not_use_a_model_for_gists(self) -> None:
        """Three of the four places that build the consolidator pass only a
        database, so the guard held by accident of wiring. A caller that passed
        a model to a Mode A engine would have got a model call on the one mode
        whose whole promise is that nothing does."""
        assert SLMConfig.for_mode(Mode.A).ccq.use_llm_gist is False

    def test_the_other_modes_still_use_one(self) -> None:
        assert SLMConfig.for_mode(Mode.B).ccq.use_llm_gist is True
        assert SLMConfig.for_mode(Mode.C).ccq.use_llm_gist is True

    def test_the_consolidator_checks_configuration_before_availability(self) -> None:
        """Order matters: availability-first means a supplied model wins over
        the mode's own instruction."""
        import inspect

        from superlocalmemory.encoding.cognitive_consolidator import (
            CognitiveConsolidator,
        )

        source = inspect.getsource(CognitiveConsolidator._step3_extract_gist)
        config_at = source.index("use_llm_gist")
        llm_at = source.index("self._llm is not None")
        assert config_at < llm_at, (
            "availability is being tested before the mode's own setting"
        )


class TestTheExplanationIsPresentWhenItIsNeeded:
    def test_mode_a_carries_a_message_naming_the_next_step(self) -> None:
        capability = llm_capability(SLMConfig.for_mode(Mode.A))

        assert capability["llm_available"] is False
        assert capability["message"], "Mode A must explain itself"
        assert "Mode B" in capability["message"]
        assert "Mode C" in capability["message"]

    def test_a_working_model_carries_no_message(self) -> None:
        """An explanation shown when nothing is wrong is noise, and noise is how
        a real warning stops being read."""
        capability = llm_capability(SLMConfig.for_mode(Mode.B), llm_reachable=True)

        assert capability["llm_available"] is True
        assert capability["message"] == ""

    def test_a_configured_model_that_did_not_answer_says_something_different(
        self,
    ) -> None:
        """"You have no model" and "your model did not respond" are different
        problems with different next steps."""
        unreachable = llm_capability(SLMConfig.for_mode(Mode.B), llm_reachable=False)
        absent = llm_capability(SLMConfig.for_mode(Mode.A))

        assert unreachable["message"]
        assert unreachable["message"] != absent["message"]
        assert "reachable" in unreachable["message"]

    def test_it_never_raises_on_a_config_it_cannot_read(self) -> None:
        """This describes a result; it must not be able to break one."""
        class Opaque:
            @property
            def mode(self):
                raise RuntimeError("no mode here")

        capability = llm_capability(Opaque())

        assert capability["llm_available"] is False
        assert capability["message"]


class TestTheSummaryRouteReturnsIt:
    def test_the_route_attaches_capability_to_its_response(self) -> None:
        """A helper nothing calls explains nothing."""
        import inspect

        from superlocalmemory.server.routes import memories

        source = inspect.getsource(memories.get_summary)
        assert "llm_capability" in source
        assert '"capability"' in source

    def test_the_dashboard_renders_the_message(self) -> None:
        """The API is not the surface the user is looking at."""
        from pathlib import Path

        import superlocalmemory

        js = (
            Path(superlocalmemory.__file__).parent / "ui" / "js" / "od-memories.js"
        ).read_text()
        assert "d.capability" in js or "capability" in js
        assert "cap.message" in js, (
            "the dashboard receives the explanation and does not show it"
        )
