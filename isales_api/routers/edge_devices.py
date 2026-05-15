"""/edge-devices — read-only view of edge-device liveness for the boss console.

Spec: arch-cloud-edge-split § device-hardware Requirement "modem-controller
      心跳与失联探测" (cloud-edge heartbeat updates Device.last_seen_at;
      cloud worker watchdog flips status to OFFLINE after 120 s of silence);
      arch-cloud-edge-split tasks.md § 9.1.

In A2 there is no separate ``edge_device`` table — one edge host can own
multiple ``Device`` rows, and each row's ``last_seen_at`` is refreshed by the
cloud-edge gRPC heartbeat (task 9.4). This endpoint exposes per-device
online/offline state so the boss console can render "which edge boxes are
reachable". When the multi-tenant edge_device table lands in C2, this
endpoint will switch to grouping by ``edge_device_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from isales_common.models import Device
from isales_common.schemas._base import AppModel, ORMModel
from sqlalchemy import select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession

router = APIRouter(prefix="/edge-devices", tags=["edge-devices"])

# Matches the cloud-edge spec watchdog threshold + worker
# isales_worker.device_watchdog.STALE_THRESHOLD_SECONDS.
ONLINE_THRESHOLD_SECONDS = 120


class EdgeDeviceStatus(ORMModel):
    id: int
    name: str
    status: str
    last_seen_at: datetime | None
    online: bool


class EdgeDeviceStatusList(AppModel):
    """Whole-fleet snapshot. v1.0 has one edge host per cloud instance, so
    the list is short (≤ N modems on that host). C2 grouping by
    ``edge_device_id`` will land alongside the multi-tenant schema."""

    threshold_seconds: int
    online_count: int
    offline_count: int
    items: list[EdgeDeviceStatus]


def _is_online(last_seen_at: datetime | None, *, now: datetime) -> bool:
    if last_seen_at is None:
        return False
    return last_seen_at >= now - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)


@router.get("/status", response_model=EdgeDeviceStatusList)
async def edge_device_status(
    session: DBSession, _user: CurrentUser
) -> EdgeDeviceStatusList:
    """Return online/offline status for every registered edge device."""

    now = datetime.now(tz=UTC)
    stmt = select(Device).order_by(Device.id.asc())
    rows = (await session.execute(stmt)).scalars().all()

    items: list[EdgeDeviceStatus] = []
    online = 0
    for row in rows:
        is_online = _is_online(row.last_seen_at, now=now)
        if is_online:
            online += 1
        items.append(
            EdgeDeviceStatus(
                id=row.id,
                name=row.name,
                status=str(row.status),
                last_seen_at=row.last_seen_at,
                online=is_online,
            )
        )

    return EdgeDeviceStatusList(
        threshold_seconds=ONLINE_THRESHOLD_SECONDS,
        online_count=online,
        offline_count=len(items) - online,
        items=items,
    )
