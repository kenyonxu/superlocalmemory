from __future__ import annotations

import pytest
from fastapi import HTTPException

from superlocalmemory.server.unified_daemon import _require_remember_profile


@pytest.mark.parametrize("requested", ["", "default"])
def test_remember_profile_guard_accepts_unbound_or_matching_request(requested):
    _require_remember_profile(requested, "default")


def test_remember_profile_guard_rejects_stale_client_before_write():
    with pytest.raises(HTTPException) as caught:
        _require_remember_profile("work", "default")

    assert caught.value.status_code == 409
    assert "profile mismatch" in str(caught.value.detail)
