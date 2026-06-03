"""Integration tests for /campaigns CRUD with nested children + devices."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from isales_common.models import (
    CallbackConfig,
    Campaign,
    Device,
    FillerPhrase,
    FillerSet,
    RoleConfig,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


def _campaign_payload(**overrides: object) -> dict[str, object]:
    """A complete CampaignBase + nested-children body."""
    base: dict[str, object] = {
        "name": "C1",
        "concurrency": 1,
        "max_silence_activations": 2,
        "silence_threshold_ms": 3000,
        "wrap_up_max_rounds": 3,
        "wrap_up_max_seconds": 60,
        "interruption_min_duration_ms": 400,
        "max_continuous_interruptions": 3,
        "continuous_interruption_strategy": "short_reply",
        "transfer_keyword_enabled": False,
        "transfer_intent_enabled": False,
        "transfer_round_enabled": False,
        "transfer_llm_enabled": False,
        "retry_max_count": 3,
        "follow_up_max_count": 0,
        "do_not_call_llm_enabled": False,
        "respect_holidays": True,
        "role_configs": [],
        "filler_sets": [],
        "callback_configs": [],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio(loop_scope="session")
class TestCampaignNestedCRUD:
    async def test_create_with_nested_then_get(self, client: AsyncClient) -> None:
        body = _campaign_payload(
            role_configs=[
                {"kind": "main", "model": "gpt-4o-mini"},
                {"kind": "referee", "model": "gpt-4o-mini"},
            ],
            filler_sets=[
                {
                    "name": "polite",
                    "sort_order": 1,
                    "phrases": [
                        {"phrase": "嗯嗯"},
                        {"phrase": "稍等"},
                    ],
                }
            ],
            callback_configs=[
                {
                    "name": "wh1",
                    "trigger": {"type": "call_ended"},
                    "url": "https://example.com/webhook",
                    "payload_template": "{}",
                    "retry_policy": {"max_attempts": 3, "intervals_seconds": [60, 120, 300]},
                }
            ],
        )
        resp = await client.post("/campaigns", json=body)
        assert resp.status_code == 201, resp.text
        cid = resp.json()["id"]

        get_resp = await client.get(f"/campaigns/{cid}")
        assert get_resp.status_code == 200
        detail = get_resp.json()
        assert len(detail["role_configs"]) == 2
        assert len(detail["filler_sets"]) == 1
        assert len(detail["filler_sets"][0]["phrases"]) == 2
        assert len(detail["callback_configs"]) == 1

    async def test_patch_replaces_children_fully(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = (
            await client.post(
                "/campaigns",
                json=_campaign_payload(
                    name="C2",
                    role_configs=[{"kind": "main", "model": "old"}],
                    filler_sets=[{"name": "old", "phrases": [{"phrase": "old"}]}],
                ),
            )
        ).json()["id"]

        patch = await client.patch(
            f"/campaigns/{cid}",
            json={
                "name": "C2-renamed",
                "role_configs": [{"kind": "extractor", "model": "new"}],
                "filler_sets": [
                    {"name": "new", "phrases": [{"phrase": "new1"}, {"phrase": "new2"}]}
                ],
            },
        )
        assert patch.status_code == 200
        body = patch.json()
        assert body["name"] == "C2-renamed"
        assert len(body["role_configs"]) == 1
        assert body["role_configs"][0]["kind"] == "extractor"
        assert len(body["filler_sets"][0]["phrases"]) == 2

        # Confirm cascading delete via direct DB read.
        sm = async_sessionmaker(clean_engine, expire_on_commit=False)
        async with sm() as session:
            old_role_count = (
                await session.execute(
                    select(RoleConfig).where(RoleConfig.campaign_id == cid)
                )
            ).scalars().all()
            assert len(old_role_count) == 1  # only 'extractor' survives
            phrases = (
                await session.execute(
                    select(FillerPhrase).join(FillerSet).where(
                        FillerSet.campaign_id == cid
                    )
                )
            ).scalars().all()
            assert len(phrases) == 2

    async def test_delete_cascades_children(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = (
            await client.post(
                "/campaigns",
                json=_campaign_payload(
                    name="C3",
                    role_configs=[{"kind": "main", "model": "x"}],
                    filler_sets=[{"name": "fs", "phrases": [{"phrase": "p"}]}],
                    callback_configs=[
                        {
                            "name": "cb",
                            "trigger": {"type": "call_ended"},
                            "url": "https://example.com",
                            "payload_template": "{}",
                            "retry_policy": {"max_attempts": 1, "intervals_seconds": [5]},
                        }
                    ],
                ),
            )
        ).json()["id"]

        del_resp = await client.delete(f"/campaigns/{cid}")
        assert del_resp.status_code == 204
        assert (await client.get(f"/campaigns/{cid}")).status_code == 404

        sm = async_sessionmaker(clean_engine, expire_on_commit=False)
        async with sm() as session:
            for model in (Campaign, RoleConfig, FillerSet, FillerPhrase, CallbackConfig):
                rows = (await session.execute(select(model))).scalars().all()
                assert rows == [], f"{model.__name__} not cascaded"

    async def test_list_pagination(self, client: AsyncClient) -> None:
        for i in range(3):
            await client.post("/campaigns", json=_campaign_payload(name=f"P{i}"))
        body = (await client.get("/campaigns", params={"page_size": 2})).json()
        assert body["total"] == 3
        assert len(body["items"]) == 2

    async def test_get_404(self, client: AsyncClient) -> None:
        assert (await client.get("/campaigns/9999")).status_code == 404

    async def test_filler_enabled_create_default_and_patch(
        self, client: AsyncClient
    ) -> None:
        # Defaults to False on create.
        cid = (
            await client.post("/campaigns", json=_campaign_payload(name="CF"))
        ).json()["id"]
        detail = (await client.get(f"/campaigns/{cid}")).json()
        assert detail["filler_enabled"] is False
        # PATCH flips it on.
        patched = await client.patch(
            f"/campaigns/{cid}", json={"filler_enabled": True}
        )
        assert patched.status_code == 200
        assert patched.json()["filler_enabled"] is True


@pytest.mark.asyncio(loop_scope="session")
class TestCampaignDevices:
    async def test_attach_and_list_and_detach(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = (
            await client.post("/campaigns", json=_campaign_payload(name="DC"))
        ).json()["id"]

        sm = async_sessionmaker(clean_engine, expire_on_commit=False)
        async with sm() as session:
            dev = Device(name="d", imei="i")
            session.add(dev)
            await session.commit()
            device_id = dev.id

        attach = await client.post(
            f"/campaigns/{cid}/devices", json={"device_id": device_id}
        )
        assert attach.status_code == 201
        assert attach.json()["device_id"] == device_id

        listing = (await client.get(f"/campaigns/{cid}/devices")).json()
        assert len(listing) == 1

        detach = await client.delete(f"/campaigns/{cid}/devices/{device_id}")
        assert detach.status_code == 204

        empty = (await client.get(f"/campaigns/{cid}/devices")).json()
        assert empty == []

    async def test_attach_to_unknown_campaign_404(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        sm = async_sessionmaker(clean_engine, expire_on_commit=False)
        async with sm() as session:
            dev = Device(name="d2", imei="i2")
            session.add(dev)
            await session.commit()
            device_id = dev.id
        resp = await client.post(
            "/campaigns/9999/devices", json={"device_id": device_id}
        )
        assert resp.status_code == 404
