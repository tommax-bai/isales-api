"""Auth tests — login success / wrong-password / expired / missing token."""

from __future__ import annotations

import time
from datetime import timedelta

import bcrypt
import pytest
from fastapi.testclient import TestClient

from isales_api.auth.jwt import sign_jwt
from isales_api.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    pw_hash = bcrypt.hashpw(b"changeme", bcrypt.gensalt()).decode()
    monkeypatch.setenv("ISALES_ADMIN_USER", "admin")
    monkeypatch.setenv("ISALES_ADMIN_PASSWORD_HASH", pw_hash)
    monkeypatch.setenv("ISALES_JWT_SECRET", "test-secret")
    app = create_app()
    return TestClient(app)


class TestLogin:
    def test_login_success_returns_token(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/login",
            data={"username": "admin", "password": "changeme"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20

    def test_login_wrong_password_401(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_login_unknown_user_401(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/login",
            data={"username": "ghost", "password": "changeme"},
        )
        assert resp.status_code == 401

    def test_login_validation_422_when_form_missing(self, client: TestClient) -> None:
        resp = client.post("/auth/login", data={})
        assert resp.status_code == 422


class TestMe:
    def test_me_with_valid_token(self, client: TestClient) -> None:
        token = client.post(
            "/auth/login",
            data={"username": "admin", "password": "changeme"},
        ).json()["access_token"]
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["sub"] == "admin"

    def test_me_missing_token_401(self, client: TestClient) -> None:
        assert client.get("/auth/me").status_code == 401

    def test_me_invalid_token_401(self, client: TestClient) -> None:
        resp = client.get(
            "/auth/me", headers={"Authorization": "Bearer not.a.real.jwt"}
        )
        assert resp.status_code == 401

    def test_me_expired_token_401(self, client: TestClient) -> None:
        token = sign_jwt({"sub": "admin"}, ttl=timedelta(seconds=-1))
        time.sleep(0.01)
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
