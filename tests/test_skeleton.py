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


def test_openapi_loads() -> None:
    app = create_app()
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
    assert "/health" in spec["paths"]
