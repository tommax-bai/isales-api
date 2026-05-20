"""Tests for /appointments — CRUD + state-machine + lead-status coupling.

Spec: openspec/changes/web-admin-ui-redesign capability `appointment`.

State-machine matrix:

    pending  --confirm-->  confirmed
    pending  --cancel--->  cancelled
    confirmed --complete-> completed (and lead → visited)
    confirmed --cancel--->  cancelled (lead UNCHANGED)
    completed/cancelled --any--> 409
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from isales_common.models import Campaign, Lead
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


async def _seed_campaign_and_lead(
    engine: AsyncEngine, *, phone: str = "+8613800138000"
) -> tuple[int, int]:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        c = Campaign(name="C")
        session.add(c)
        await session.flush()
        ld = Lead(campaign_id=c.id, phone=phone, name="Alice")
        session.add(ld)
        await session.commit()
        return c.id, ld.id


def _appointment_payload(lead_id: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "lead_id": lead_id,
        "appointment_time": (
            datetime.now(tz=timezone.utc) + timedelta(days=3)
        ).isoformat(),
        "store_address": "上海市浦东新区世纪大道 100 号",
        "directions": "地铁 2 号线世纪大道站 4 号口出步行 200 米",
        "notes": "客户偏好下午时段",
    }
    base.update(overrides)
    return base


async def _read_lead_status(engine: AsyncEngine, lead_id: int) -> str:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        obj = await session.get(Lead, lead_id)
        assert obj is not None
        return obj.status.value if hasattr(obj.status, "value") else str(obj.status)


@pytest.mark.asyncio(loop_scope="session")
class TestAppointmentsCrud:
    async def test_create_advances_lead_to_appointed(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        resp = await client.post(
            "/appointments", json=_appointment_payload(lead_id)
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["lead_name"] == "Alice"
        assert body["lead_phone"] == "+8613800138000"
        assert await _read_lead_status(clean_engine, lead_id) == "appointed"

    async def test_create_missing_lead_returns_404(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        await _seed_campaign_and_lead(clean_engine)
        resp = await client.post(
            "/appointments", json=_appointment_payload(99999)
        )
        assert resp.status_code == 404

    async def test_list_filter_by_status(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lid1 = await _seed_campaign_and_lead(clean_engine, phone="+8613000000001")
        _, lid2 = await _seed_campaign_and_lead(clean_engine, phone="+8613000000002")
        a1 = (await client.post("/appointments", json=_appointment_payload(lid1))).json()
        await client.post("/appointments", json=_appointment_payload(lid2))
        # advance a1 to confirmed
        await client.patch(
            f"/appointments/{a1['id']}/status", json={"action": "confirm"}
        )
        pending = await client.get("/appointments", params={"status": "pending"})
        assert pending.json()["total"] == 1
        confirmed = await client.get("/appointments", params={"status": "confirmed"})
        assert confirmed.json()["total"] == 1

    async def test_patch_field_edit_blocked_on_terminal(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        # cancel → terminal
        await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "cancel"}
        )
        resp = await client.patch(
            f"/appointments/{a['id']}", json={"notes": "edited"}
        )
        assert resp.status_code == 409

    async def test_delete_blocked_when_confirmed(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "confirm"}
        )
        resp = await client.delete(f"/appointments/{a['id']}")
        assert resp.status_code == 409

    async def test_delete_allowed_when_pending(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        resp = await client.delete(f"/appointments/{a['id']}")
        assert resp.status_code == 204


@pytest.mark.asyncio(loop_scope="session")
class TestAppointmentStateMachine:
    async def test_confirm_pending(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        resp = await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "confirm"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"

    async def test_complete_requires_confirmed(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        # pending → complete is illegal
        resp = await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "complete"}
        )
        assert resp.status_code == 409

    async def test_complete_advances_lead_to_visited(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "confirm"}
        )
        resp = await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "complete"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        assert await _read_lead_status(clean_engine, lead_id) == "visited"

    async def test_cancel_from_pending(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        # lead is now appointed
        resp = await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "cancel"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        # lead status MUST NOT regress on cancel
        assert await _read_lead_status(clean_engine, lead_id) == "appointed"

    async def test_cancel_from_confirmed_does_not_touch_lead(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "confirm"}
        )
        await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "cancel"}
        )
        assert await _read_lead_status(clean_engine, lead_id) == "appointed"

    async def test_terminal_rejects_further_actions(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        await client.patch(
            f"/appointments/{a['id']}/status", json={"action": "cancel"}
        )
        # cancelled → any action is 409
        for action in ("confirm", "complete", "cancel"):
            resp = await client.patch(
                f"/appointments/{a['id']}/status", json={"action": action}
            )
            assert resp.status_code == 409, action

    async def test_status_cannot_be_set_directly(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        """PATCH /appointments/{id} (field-edit) MUST NOT accept status —
        AppointmentUpdate has extra='forbid' so the request is rejected at
        validation time. The state-machine is the only way in."""
        _, lead_id = await _seed_campaign_and_lead(clean_engine)
        a = (await client.post("/appointments", json=_appointment_payload(lead_id))).json()
        resp = await client.patch(
            f"/appointments/{a['id']}",
            json={"status": "completed", "notes": "ok"},
        )
        assert resp.status_code == 422
        # original status preserved
        got = await client.get(f"/appointments/{a['id']}")
        assert got.json()["status"] == "pending"


@pytest.mark.asyncio(loop_scope="session")
class TestAppointmentAuth:
    async def test_unauth_returns_401(self, clean_engine: AsyncEngine) -> None:
        from httpx import ASGITransport, AsyncClient as _AC

        from isales_api.main import create_app

        app = create_app()
        app.state.engine = clean_engine
        app.state.sessionmaker = async_sessionmaker(clean_engine, expire_on_commit=False)
        # no dependency_override → current_user is required → 401
        async with _AC(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/appointments")
            assert resp.status_code == 401
