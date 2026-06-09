"""Tests for /leads, /holidays routers."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from isales_common.models import Campaign
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


async def _seed_campaign(engine: AsyncEngine, name: str = "C") -> int:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        c = Campaign(name=name)
        session.add(c)
        await session.commit()
        return c.id


@pytest.mark.asyncio(loop_scope="session")
class TestLeads:
    async def test_create_get_patch_delete(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        created = await client.post(
            "/leads",
            json={"campaign_id": cid, "phone": "+8613800138000"},
        )
        assert created.status_code == 201, created.text
        lid = created.json()["id"]
        assert (await client.get(f"/leads/{lid}")).status_code == 200
        patched = await client.patch(f"/leads/{lid}", json={"name": "Alice"})
        assert patched.json()["name"] == "Alice"
        assert (await client.delete(f"/leads/{lid}")).status_code == 204

    async def test_list_filter_by_campaign(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        c1 = await _seed_campaign(clean_engine, "X")
        c2 = await _seed_campaign(clean_engine, "Y")
        for cid in (c1, c1, c2):
            await client.post(
                "/leads", json={"campaign_id": cid, "phone": "+8613000000001"}
            )
        body = (await client.get("/leads", params={"campaign_id": c1})).json()
        assert body["total"] == 2

    async def test_import_csv_partial_success(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        csv_text = (
            "campaign_id,phone,name,custom_data\n"
            f"{cid},+8613800000001,Alice,\n"
            f"{cid},+8613800000002,Bob,\n"
            f"{cid},,empty-phone,\n"   # row 4: error
            "abc,+8613800000004,bad-cid,\n"  # row 5: error
            f"{cid},+8613800000005,Carol,{{\"k\":1}}\n"
        )
        resp = await client.post(
            "/leads/import",
            files={"file": ("leads.csv", csv_text, "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success_count"] == 3
        assert body["error_count"] == 2
        assert {e["row"] for e in body["errors"]} == {4, 5}

    async def test_import_missing_columns_400(self, client: AsyncClient) -> None:
        bad = await client.post(
            "/leads/import",
            files={"file": ("bad.csv", "phone\n+8613800000001\n", "text/csv")},
        )
        assert bad.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
class TestHolidays:
    async def test_crud(self, client: AsyncClient) -> None:
        created = await client.post(
            "/holidays",
            json={"date": "2026-10-01", "name": "国庆", "region": "CN"},
        )
        assert created.status_code == 201
        hid = created.json()["id"]
        listed = (await client.get("/holidays")).json()
        assert listed["total"] == 1
        patch = await client.patch(f"/holidays/{hid}", json={"name": "国庆节"})
        assert patch.json()["name"] == "国庆节"
        assert (await client.delete(f"/holidays/{hid}")).status_code == 204

    async def test_invalid_date_422(self, client: AsyncClient) -> None:
        bad = await client.post(
            "/holidays",
            json={"date": "not-a-date", "name": "x"},
        )
        assert bad.status_code == 422
