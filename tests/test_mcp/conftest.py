"""Explicit feature opt-ins for media behavior tests."""

import sys

import pytest


@pytest.fixture
def media_enabled(_block_live_slm_home_writes):
    """Persist the real switch in this test's isolated root, without installing."""
    from superlocalmemory.runtimes import features

    # Profile definitions snapshot the switch at import. A module first loaded
    # by this opt-in must not leave that snapshot in later images-off tests.
    module_name = "superlocalmemory.mcp.profiles"
    previous = sys.modules.get(module_name)
    try:
        status = features.enable_media(source="api", start_install=False)
        assert status["enabled"], status
        assert features.media_enabled()
        yield
    finally:
        if previous is None:
            loaded = sys.modules.pop(module_name, None)
            package = sys.modules.get("superlocalmemory.mcp")
            if loaded is not None and getattr(package, "profiles", None) is loaded:
                delattr(package, "profiles")
