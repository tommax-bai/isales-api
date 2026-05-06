"""Tests for /campaigns/{id}/start and /pause — Redis queue handoff."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from isales_common.models import Campaign
from isales_common.utils.redis import get_redis as common_redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from isales_api.auth.deps import current_user
from isales_api.main import create_app
from isales_api.routers.campaigns import CAMPAIGN_CONTROL_QUEUE


@pytest_asyncio.fixture(loop_scope="session")
async def redis_client() -> AsyncIterator[object]:
    url = os.environ.get("ISALES_REDIS_URL", "redis://localhost:6379/0")
    try:
        client = common_redis(url)
        await client.ping()
    except Exception as exc:
        pytest.skip(f"Redis not reachable: {exc}")
    await client.delete(CAMPAIGN_CONTROL_QUEUE)
    yield client
    await client.delete(CAMPAIGN_CONTROL_QUEUE)
    await client.close()


@pytest_asyncio.fixture(loop_scope="session")
async def client_with_redis(
    clean_engine: AsyncEngine, redis_client: object
) -> AsyncIterator[AsyncClient]:
    """Variant of the base client fixture that also installs a Redis client on
    app.state so the start/pause routes can lpush without needing a network
    redis URL env."""
    app = create_app()
    app.state.engine = clean_engine
    app.state.sessionmaker = async_sessionmaker(clean_engine, expire_on_commit=False)
    app.state.redis = redis_client
    app.dependency_overrides[current_user] = lambda: {"sub": "test-admin"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_campaign(engine: AsyncEngine) -> int:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        c = Campaign(name="X")
        session.add(c)
        await session.commit()
        return c.id


@pytest.mark.asyncio(loop_scope="session")
async def test_start_pushes_message(
    client_with_redis: AsyncClient,
    clean_engine: AsyncEngine,
    redis_client: object,
) -> None:
    cid = await _seed_campaign(clean_engine)
    resp = await client_with_redis.post(f"/campaigns/{cid}/start")
    assert resp.status_code == 202

    raw = await redis_client.rpop(CAMPAIGN_CONTROL_QUEUE)  # type: ignore[union-attr]
    assert raw is not None
    body = json.loads(raw)
    assert body["type"] == "start"
    assert body["campaign_id"] == cid
    assert body["schema_version"] == 1
    assert "message_id" in body


@pytest.mark.asyncio(loop_scope="session")
async def test_pause_pushes_message(
    client_with_redis: AsyncClient,
    clean_engine: AsyncEngine,
    redis_client: object,
) -> None:
    cid = await _seed_campaign(clean_engine)
    resp = await client_with_redis.post(f"/campaigns/{cid}/pause")
    assert resp.status_code == 202
    body = json.loads(await redis_client.rpop(CAMPAIGN_CONTROL_QUEUE))  # type: ignore[union-attr]
    assert body["type"] == "pause"
    assert body["campaign_id"] == cid


@pytest.mark.asyncio(loop_scope="session")
async def test_start_unknown_campaign_404(
    client_with_redis: AsyncClient,
) -> None:
    resp = await client_with_redis.post("/campaigns/9999/start")
    assert resp.status_code == 404
