"""Integration-level assertions for the authenticated dashboard surface."""

from __future__ import annotations

from app.api.dashboard_routes import router as dashboard_router
from app.main import app


def test_dashboard_contract_contains_tenant_safe_read_and_control_routes() -> None:
    paths = set(app.openapi()["paths"])
    expected = {
        "/api/v1/dashboard/runs",
        "/api/v1/dashboard/runs/{run_id}/replay",
        "/api/v1/dashboard/runs/{run_id}/intervention",
        "/api/v1/dashboard/provider-profiles",
        "/api/v1/dashboard/policies",
        "/api/v1/dashboard/policies/{policy_set_id}/versions",
        "/api/v1/dashboard/policies/{policy_set_id}/versions/{version_number}/publish",
        "/api/v1/dashboard/events",
        "/api/v1/dashboard/metrics",
        "/api/v1/dashboard/model-evaluations",
    }
    assert expected <= paths


def test_live_feed_is_registered_as_a_websocket_route() -> None:
    websocket_paths = {
        route.path
        for route in dashboard_router.routes
        if getattr(route, "path", None) == "/api/v1/dashboard/live"
    }
    assert websocket_paths == {"/api/v1/dashboard/live"}
