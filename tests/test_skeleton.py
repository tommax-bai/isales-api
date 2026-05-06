"""Smoke tests for PR #1 skeleton."""

from __future__ import annotations

from fastapi.testclient import TestClient

from isales_api.main import create_app


def test_health() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_openapi_documents_all_routes() -> None:
    app = create_app()
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
    paths = set(spec["paths"].keys())
    expected = {
        "/health",
        "/auth/login",
        "/auth/me",
        "/campaigns",
        "/campaigns/{campaign_id}",
        "/campaigns/{campaign_id}/devices",
        "/campaigns/{campaign_id}/devices/{device_id}",
        "/campaigns/{campaign_id}/start",
        "/campaigns/{campaign_id}/pause",
        "/leads",
        "/leads/import",
        "/leads/{lead_id}",
        "/voice-models",
        "/voice-models/{voice_id}",
        "/voice-models/{voice_id}/sample",
        "/holidays",
        "/holidays/{holiday_id}",
        "/handoff-tasks",
        "/handoff-tasks/{task_id}",
        "/handoff-tasks/{task_id}/pick-up",
        "/handoff-tasks/{task_id}/complete",
        "/calls",
        "/calls/{call_id}",
        "/calls/{call_id}/summary",
        "/analytics/answer-rate",
        "/analytics/goal-rate",
        "/analytics/duration-distribution",
    }
    missing = expected - paths
    assert not missing, f"missing routes in OpenAPI: {sorted(missing)}"

    schemas = spec["components"]["schemas"]
    for required in (
        "TokenResponse",
        "CampaignDetailRead",
        "LeadRead",
        "CallRecordRead",
        "AnswerRate",
    ):
        assert required in schemas, f"{required} missing from OpenAPI components"
