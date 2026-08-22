"""What the dashboard says must be a function of what the daemon reports.

Two ways that stopped being true. The health card read a field that is the
literal string "ok" on every reply the daemon is alive enough to send, so a
daemon that had not finished starting, or whose search was not answering,
displayed as Healthy. And the page's version marker was substituted under a
name the page does not contain, so the script that reloads stale assets after
an upgrade saw an unsubstituted placeholder and gave up — on every upgrade,
since the placeholder was mangled.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import superlocalmemory

_UI = pathlib.Path(superlocalmemory.__file__).parent / "ui"


class TestTheVersionMarkerReachesThePage:
    def _shipped_placeholder(self) -> str:
        html = (_UI / "index.html").read_text()
        found = re.search(r'<meta name="slm-version" content="([^"]+)">', html)
        assert found, "the page carries no version marker at all"
        return found.group(1)

    def test_the_page_and_the_script_agree_on_the_placeholder(self):
        core = (_UI / "js" / "core.js").read_text()
        guard = re.search(r"pageVersion === '([^']+)'", core)
        assert guard, "the script no longer guards on an unsubstituted marker"
        assert guard.group(1) == self._shipped_placeholder()

    @pytest.mark.parametrize("module", ["unified_daemon", "api", "ui"])
    def test_every_serve_path_substitutes_the_placeholder_the_page_contains(
        self, module,
    ):
        """Three paths serve this page and each substituted a different string.
        Two of them were mangled variants that appear nowhere in the page."""
        source = (
            pathlib.Path(superlocalmemory.__file__).parent
            / "server" / f"{module}.py"
        ).read_text()
        used = set(re.findall(r"__SLM_[A-Za-z_]*__", source))
        unknown = used - {self._shipped_placeholder()}
        assert not unknown, (
            f"{module}.py substitutes {sorted(unknown)}, which the page does "
            f"not contain, so the version never reaches it"
        )

    def test_rendering_the_page_puts_a_real_version_in_it(self):
        from superlocalmemory.server.asset_versions import render_index

        placeholder = self._shipped_placeholder()
        rendered = render_index(
            _UI / "index.html", _UI, substitutions={placeholder: "9.9.9-test"},
        )
        served = re.search(
            r'<meta name="slm-version" content="([^"]+)">', rendered,
        ).group(1)
        assert served == "9.9.9-test"


class TestTheHealthCardReportsRealReadiness:
    def _script(self) -> str:
        return (_UI / "js" / "od-health.js").read_text()

    def test_it_does_not_decide_from_a_field_that_never_varies(self):
        """`status` is "ok" on every reply. Deciding from it means the card is
        a constant dressed up as a measurement."""
        script = self._script()
        card = script[script.index("Card 1: Daemon"):script.index("Card 2:")]
        assert "runtime_state" in card, (
            "the daemon card does not read the field that reports readiness"
        )

    def test_every_readiness_the_daemon_can_report_has_a_label(self):
        """The daemon has four readiness values; a card that labels one of them
        and falls through on the rest reports the wrong thing three times."""
        daemon_source = (
            pathlib.Path(superlocalmemory.__file__).parent
            / "server" / "unified_daemon.py"
        ).read_text()
        reported = set(re.findall(r'runtime_state = "([a-z_]+)"', daemon_source))
        assert reported, "the daemon no longer reports a readiness state"

        script = self._script()
        for state in reported:
            assert state in script, (
                f"the daemon can report readiness {state!r} and the dashboard "
                f"has no label for it"
            )

    def test_the_daemon_reports_how_far_behind_the_graph_copy_is(self):
        """A drain that stops advancing is the failure that does not announce
        itself: every other signal stays green while answers get worse."""
        daemon_source = (
            pathlib.Path(superlocalmemory.__file__).parent
            / "server" / "unified_daemon.py"
        ).read_text()
        assert '"projection": _projection_health(application)' in daemon_source
        assert "def _projection_health(" in daemon_source

    def test_that_report_never_raises(self):
        """A health endpoint that fails because one field could not be computed
        is worse than the missing field."""
        from superlocalmemory.server.unified_daemon import _projection_health

        class _Exploding:
            class state:
                def __getattr__(self, name):
                    raise RuntimeError("nothing works")

        result = _projection_health(_Exploding())
        assert isinstance(result, dict)
        assert result.get("available") is False
