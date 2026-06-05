"""Tests for per-campaign config admin endpoints.

Spec: openspec/changes/web-admin-campaign-workflow — `/role-configs`,
`/prompt-versions`, `/filler-sets` CRUD + `/campaigns/{id}/progress`.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from isales_common.enums import LeadStatus
from isales_common.models import Campaign, Lead
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


async def _seed_campaign(engine: AsyncEngine, name: str = "C") -> int:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        c = Campaign(name=name)
        session.add(c)
        await session.commit()
        return c.id


@pytest.mark.asyncio(loop_scope="session")
class TestRoleConfigs:
    async def test_crud(self, client: AsyncClient, clean_engine: AsyncEngine) -> None:
        cid = await _seed_campaign(clean_engine)
        created = await client.post(
            "/role-configs",
            json={
                "campaign_id": cid,
                "kind": "main",
                "model": "doubao-pro-32k",
                "temperature": 0.7,
                "top_p": 1.0,
                "enabled": True,
            },
        )
        assert created.status_code == 201, created.text
        rid = created.json()["id"]
        assert (await client.get(f"/role-configs/{rid}")).status_code == 200
        patched = await client.patch(f"/role-configs/{rid}", json={"temperature": 1.2})
        assert patched.json()["temperature"] == 1.2
        body = (
            await client.get("/role-configs", params={"campaign_id": cid, "kind": "main"})
        ).json()
        assert body["total"] == 1
        assert (await client.delete(f"/role-configs/{rid}")).status_code == 204

    async def test_kind_filter(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        for kind in ("main", "main", "referee"):
            await client.post(
                "/role-configs",
                json={
                    "campaign_id": cid,
                    "kind": kind,
                    # referee/restructure rows need a routing label.
                    "label": "j1" if kind == "referee" else None,
                    "model": "m",
                    "temperature": 0.5,
                    "top_p": 1.0,
                    "enabled": True,
                },
            )
        body = (
            await client.get("/role-configs", params={"campaign_id": cid, "kind": "main"})
        ).json()
        assert body["total"] == 2


@pytest.mark.asyncio(loop_scope="session")
class TestPromptVersions:
    async def test_active_exclusivity(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        # prompt_version has no FK on scope_id; any int is a valid scope.
        v1 = (
            await client.post(
                "/prompt-versions",
                json={"scope_type": "main", "scope_id": 1, "content": "v1", "is_active": True},
            )
        ).json()
        v2 = (
            await client.post(
                "/prompt-versions",
                json={"scope_type": "main", "scope_id": 1, "content": "v2", "is_active": True},
            )
        ).json()
        # Setting v2 active deactivates v1 in the same scope.
        assert (await client.get(f"/prompt-versions/{v1['id']}")).json()["is_active"] is False
        assert (await client.get(f"/prompt-versions/{v2['id']}")).json()["is_active"] is True

    async def test_scope_filter(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        await client.post(
            "/prompt-versions",
            json={"scope_type": "main", "scope_id": 7, "content": "x"},
        )
        await client.post(
            "/prompt-versions",
            json={"scope_type": "referee", "scope_id": 7, "content": "y"},
        )
        body = (
            await client.get(
                "/prompt-versions", params={"scope_type": "main", "scope_id": 7}
            )
        ).json()
        assert body["total"] == 1


@pytest.mark.asyncio(loop_scope="session")
class TestFillerSets:
    async def test_set_and_phrase_crud(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        fs = (
            await client.post(
                "/filler-sets",
                json={"campaign_id": cid, "name": "默认垫词", "sort_order": 0},
            )
        ).json()
        sid = fs["id"]
        ph = await client.post(
            f"/filler-sets/{sid}/phrases",
            json={"filler_set_id": sid, "phrase": "嗯嗯"},
        )
        assert ph.status_code == 201, ph.text
        pid = ph.json()["id"]
        assert len((await client.get(f"/filler-sets/{sid}/phrases")).json()) == 1
        patched = await client.patch(
            f"/filler-sets/phrases/{pid}", json={"phrase": "好的好的"}
        )
        assert patched.json()["phrase"] == "好的好的"
        assert (await client.delete(f"/filler-sets/phrases/{pid}")).status_code == 204
        assert (await client.delete(f"/filler-sets/{sid}")).status_code == 204

    async def test_phrase_path_body_mismatch_400(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        sid = (
            await client.post(
                "/filler-sets", json={"campaign_id": cid, "name": "s"}
            )
        ).json()["id"]
        resp = await client.post(
            f"/filler-sets/{sid}/phrases",
            json={"filler_set_id": sid + 999, "phrase": "x"},
        )
        assert resp.status_code == 400

    async def test_campaign_filter(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        c1 = await _seed_campaign(clean_engine, "X")
        c2 = await _seed_campaign(clean_engine, "Y")
        await client.post("/filler-sets", json={"campaign_id": c1, "name": "a"})
        await client.post("/filler-sets", json={"campaign_id": c2, "name": "b"})
        assert (
            await client.get("/filler-sets", params={"campaign_id": c1})
        ).json()["total"] == 1


@pytest.mark.asyncio(loop_scope="session")
class TestCampaignProgress:
    async def test_status_counts(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        sm = async_sessionmaker(clean_engine, expire_on_commit=False)
        async with sm() as session:
            session.add_all(
                [
                    Lead(campaign_id=cid, phone="13800000001", status=LeadStatus.NEW),
                    Lead(campaign_id=cid, phone="13800000002", status=LeadStatus.NEW),
                    Lead(
                        campaign_id=cid,
                        phone="13800000003",
                        status=LeadStatus.CALLING,
                    ),
                ]
            )
            await session.commit()
        body = (await client.get(f"/campaigns/{cid}/progress")).json()
        assert body["total"] == 3
        assert body["status_counts"]["new"] == 2
        assert body["status_counts"]["calling"] == 1
        # is_active 来自 Redis SET；测试 client 无 redis → 降级为 False。
        assert body["is_active"] is False

    async def test_missing_campaign_404(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        assert (await client.get("/campaigns/99999/progress")).status_code == 404


@pytest.mark.asyncio(loop_scope="session")
class TestConfigEndpointsAuth:
    async def test_unauth_401(self, clean_engine: AsyncEngine) -> None:
        from isales_api.main import create_app

        app = create_app()
        app.state.engine = clean_engine
        app.state.sessionmaker = async_sessionmaker(clean_engine, expire_on_commit=False)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            for path in ("/role-configs", "/prompt-versions", "/filler-sets"):
                resp = await ac.get(path)
                assert resp.status_code == 401, path
