"""Tests for V3 API endpoints — UI revamp."""
import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")


def test_v3_api_importable():
    from superlocalmemory.server.routes.v3_api import router
    assert router is not None


def test_v3_api_has_routes():
    from superlocalmemory.server.routes.v3_api import router
    route_paths = [r.path for r in router.routes]
    assert "/api/v3/dashboard" in route_paths
    assert "/api/v3/mode" in route_paths
    assert "/api/v3/providers" in route_paths
    assert "/api/v3/provider" in route_paths
    assert "/api/v3/recall/trace" in route_paths
    assert "/api/v3/trust/dashboard" in route_paths
    assert "/api/v3/math/health" in route_paths
    assert "/api/v3/auto-capture/config" in route_paths
    assert "/api/v3/auto-recall/config" in route_paths
    assert "/api/v3/ide/status" in route_paths
    assert "/api/v3/ide/connect" in route_paths
