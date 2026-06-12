"""Integration tests for /api/provider-credentials.

Spec: provider-credential capability § "Admin CRUD HTTP 端点"。

PG-backed (real ON CONFLICT DO UPDATE)；client 自带 admin JWT override
(test-admin)。每个测试方法跑前 TRUNCATE provider_credential。
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestProviderCredentialsRouter:
    async def test_list_empty(self, client: AsyncClient):
        r = await client.get("/provider-credentials")
        assert r.status_code == 200
        assert r.json() == []

    async def test_upsert_creates_new(self, client: AsyncClient):
        r = await client.post(
            "/provider-credentials",
            json={
                "provider_id": "volcengine",
                "field_name": "app_key",
                "plaintext_value": "vc-test-key-abcdef1234",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["provider_id"] == "volcengine"
        assert body["field_name"] == "app_key"
        assert body["masked_value"] == "vc-t" + "*" * 8 + "1234"
        assert body["updated_by"] == "test-admin"
        # response 不含明文 / cipher
        assert "vc-test-key" not in r.text
        assert "plaintext_value" not in body
        assert "cipher_text" not in body

    async def test_upsert_idempotent(self, client: AsyncClient):
        # 同 (provider_id, field_name) 两次 upsert → 行数不增
        await client.post(
            "/provider-credentials",
            json={"provider_id": "dashscope", "field_name": "api_key", "plaintext_value": "sk-aaaa"},
        )
        r2 = await client.post(
            "/provider-credentials",
            json={"provider_id": "dashscope", "field_name": "api_key", "plaintext_value": "sk-bbbb"},
        )
        assert r2.status_code == 201
        # 列表应只有 1 行 (upsert 覆盖)
        rl = await client.get("/provider-credentials")
        assert rl.status_code == 200
        rows = rl.json()
        assert len(rows) == 1
        # masked 反映新值
        assert rows[0]["masked_value"] == "********"  # "sk-bbbb" 长度 7 < 8 → 全 mask

    async def test_volcengine_speech_provider_accepted(self, client: AsyncClient):
        # split-model-and-speech-provider-config: 语音 provider_id 白名单含
        # volcengine_speech, 且 tts_resource_id 是合法 field_name。
        r = await client.post(
            "/provider-credentials",
            json={
                "provider_id": "volcengine_speech",
                "field_name": "app_token",
                "plaintext_value": "speech-token-abcdef1234",
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["provider_id"] == "volcengine_speech"

        r2 = await client.post(
            "/provider-credentials",
            json={
                "provider_id": "volcengine_speech",
                "field_name": "tts_resource_id",
                "plaintext_value": "seed-tts-2.0",
            },
        )
        assert r2.status_code == 201, r2.text
        assert r2.json()["field_name"] == "tts_resource_id"

    async def test_unknown_provider_id_rejected(self, client: AsyncClient):
        r = await client.post(
            "/provider-credentials",
            json={
                "provider_id": "anthropic",
                "field_name": "api_key",
                "plaintext_value": "test",
            },
        )
        assert r.status_code == 422
        assert "unknown_provider_id" in r.text

    async def test_unknown_field_name_rejected(self, client: AsyncClient):
        r = await client.post(
            "/provider-credentials",
            json={
                "provider_id": "volcengine",
                "field_name": "secret_handshake",
                "plaintext_value": "x",
            },
        )
        assert r.status_code == 422
        assert "unknown_field_name" in r.text

    async def test_list_by_provider_filters(self, client: AsyncClient):
        # 灌两 provider 各几行
        await client.post(
            "/provider-credentials",
            json={"provider_id": "volcengine", "field_name": "app_key", "plaintext_value": "vc-aaa1234"},
        )
        await client.post(
            "/provider-credentials",
            json={"provider_id": "volcengine", "field_name": "app_token", "plaintext_value": "vc-token-xyz"},
        )
        await client.post(
            "/provider-credentials",
            json={"provider_id": "dashscope", "field_name": "api_key", "plaintext_value": "sk-dashscope123"},
        )
        r = await client.get("/provider-credentials/volcengine")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 2
        assert {row["field_name"] for row in rows} == {"app_key", "app_token"}
        assert all(row["provider_id"] == "volcengine" for row in rows)

    async def test_list_by_provider_unknown_404_or_422(self, client: AsyncClient):
        # 未知 provider_id 是 422 (语义校验) 不是 404 (因为返回[] 没空间区分)
        r = await client.get("/provider-credentials/anthropic")
        assert r.status_code == 422

    async def test_delete(self, client: AsyncClient):
        r = await client.post(
            "/provider-credentials",
            json={"provider_id": "dashscope", "field_name": "api_key", "plaintext_value": "sk-tobedeleted"},
        )
        cred_id = r.json()["id"]

        d = await client.delete(f"/provider-credentials/{cred_id}")
        assert d.status_code == 204

        rl = await client.get("/provider-credentials")
        assert rl.json() == []

    async def test_delete_404(self, client: AsyncClient):
        d = await client.delete("/provider-credentials/9999")
        assert d.status_code == 404

    async def test_reload_hint(self, client: AsyncClient):
        r = await client.post("/provider-credentials/reload-hint")
        assert r.status_code == 202
        assert "restart" in r.json()["message"].lower()

    async def test_auth_required(self, client: AsyncClient):
        # client fixture 注入 admin override；要测真 401 得创建无 override 的 app
        # 这里跳过 — 已被 /api/* 全局 401 测试覆盖 (test_auth.py)
        pass
