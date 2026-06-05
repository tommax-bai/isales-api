"""Integration tests for POST /campaigns/tts-preview (greeting 试听).

Happy-path + validation tests monkeypatch ``build_volcengine_tts`` with a fake
provider so no real vendor call happens. The missing-credential test exercises
the real constructor against the (truncated) ``provider_credential`` table.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient


class _FakeTTS:
    """Stand-in for VolcengineTTSProvider: yields PCM, exposes sample_rate."""

    sample_rate = 16000

    async def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        yield b"\x01\x02" * 160
        yield b"\x03\x04" * 160

    async def aclose(self) -> None:
        pass


async def test_tts_preview_returns_wav(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "isales_api.routers.campaigns.build_volcengine_tts",
        lambda store: _FakeTTS(),
    )
    resp = await client.post(
        "/campaigns/tts-preview",
        json={"text": "您好，我是智联招聘的小雨", "voice_id": "zh_female_xiaohe_uranus_bigtts"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    # Valid RIFF/WAVE container with real audio payload past the 44-byte header.
    assert resp.content[:4] == b"RIFF"
    assert resp.content[8:12] == b"WAVE"
    assert len(resp.content) > 44


async def test_tts_preview_rejects_long_text_without_calling_vendor(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"n": 0}

    def _spy(store: object) -> _FakeTTS:
        called["n"] += 1
        return _FakeTTS()

    monkeypatch.setattr(
        "isales_api.routers.campaigns.build_volcengine_tts", _spy
    )
    resp = await client.post(
        "/campaigns/tts-preview",
        json={"text": "x" * 201, "voice_id": "v"},
    )
    assert resp.status_code == 422
    assert called["n"] == 0  # schema rejected before any vendor construction


async def test_tts_preview_requires_voice_id(client: AsyncClient) -> None:
    resp = await client.post(
        "/campaigns/tts-preview",
        json={"text": "hi", "voice_id": ""},
    )
    assert resp.status_code == 422


async def test_tts_preview_empty_text_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/campaigns/tts-preview",
        json={"text": "", "voice_id": "v"},
    )
    assert resp.status_code == 422


async def test_tts_preview_credential_missing_returns_400(client: AsyncClient) -> None:
    """No volcengine credential configured → 400, not 500."""
    resp = await client.post(
        "/campaigns/tts-preview",
        json={"text": "您好", "voice_id": "v"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "tts_credential_not_configured"
