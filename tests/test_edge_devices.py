"""Tests for /edge-devices/status.

Covers: empty fleet, one online + one offline, NULL last_seen_at treated as
offline, threshold boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from isales_common.enums import DeviceStatus
from isales_common.models import Device
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


async def _seed_devices(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, *, ages_seconds: list[int | None]
) -> None:
    """Seed N devices; ``ages_seconds[i]`` becomes ``now - i seconds`` for
    device i. ``None`` leaves ``last_seen_at`` NULL.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with sm() as session:
        for i, age in enumerate(ages_seconds):
            last_seen = None if age is None else now - timedelta(seconds=age)
            session.add(
                Device(
                    name=f"edge-mac-mini-{i}",
                    status=DeviceStatus.IDLE,
                    last_seen_at=last_seen,
                )
            )
        await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_fleet_returns_zero_counts(
    client: AsyncClient, clean_engine: AsyncEngine
) -> None:
    body = (await client.get("/edge-devices/status")).json()
    assert body == {
        "threshold_seconds": 120,
        "online_count": 0,
        "offline_count": 0,
        "items": [],
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_split_online_offline_and_null(
    client: AsyncClient, clean_engine: AsyncEngine
) -> None:
    # 30s old → online, 200s old → offline, None → offline
    await _seed_devices(clean_engine, ages_seconds=[30, 200, None])

    body = (await client.get("/edge-devices/status")).json()
    assert body["threshold_seconds"] == 120
    assert body["online_count"] == 1
    assert body["offline_count"] == 2
    assert len(body["items"]) == 3
    by_name = {item["name"]: item for item in body["items"]}
    assert by_name["edge-mac-mini-0"]["online"] is True
    assert by_name["edge-mac-mini-1"]["online"] is False
    assert by_name["edge-mac-mini-2"]["online"] is False
    assert by_name["edge-mac-mini-2"]["last_seen_at"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_boundary_just_inside_threshold_is_online(
    client: AsyncClient, clean_engine: AsyncEngine
) -> None:
    # 119s ago → online; 121s ago → offline
    await _seed_devices(clean_engine, ages_seconds=[119, 121])

    body = (await client.get("/edge-devices/status")).json()
    assert body["online_count"] == 1
    assert body["offline_count"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_items_sorted_by_id(
    client: AsyncClient, clean_engine: AsyncEngine
) -> None:
    await _seed_devices(clean_engine, ages_seconds=[10, 20, 30])
    body = (await client.get("/edge-devices/status")).json()
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids)
