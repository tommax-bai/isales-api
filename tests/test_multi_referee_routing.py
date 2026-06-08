"""Tests for multi-referee CRUD + routing-rules config (engine-multi-referee §5.4)."""

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


def _role(cid: int, kind: str, label: str | None = None) -> dict:
    return {
        "campaign_id": cid,
        "kind": kind,
        "label": label,
        "model": "gpt-4o-mini",
        "temperature": 0.5,
        "top_p": 1.0,
        "enabled": True,
    }


async def _post_role(client: AsyncClient, cid: int, kind: str, label: str | None = None):
    return await client.post("/role-configs", json=_role(cid, kind, label))


GOAL_RULE = {
    "referee": "main_judge",
    "match": ["goal_achieved"],
    "action": {"type": "transition", "to": "goal_achieved", "goal_type": "appointment"},
}


@pytest.mark.asyncio(loop_scope="session")
class TestMultiRefereeCRUD:
    async def test_two_referees_with_distinct_labels(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        for label in ("intent", "reject"):
            r = await _post_role(client, cid, "referee", label)
            assert r.status_code == 201, r.text
        resp = await client.get("/role-configs", params={"campaign_id": cid, "kind": "referee"})
        assert resp.json()["total"] == 2

    async def test_referee_without_label_rejected(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        r = await _post_role(client, cid, "referee", None)
        assert r.status_code == 422
        assert r.json()["detail"] == "role_label_required"

    async def test_duplicate_referee_label_rejected(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        r1 = await _post_role(client, cid, "referee", "dup")
        assert r1.status_code == 201
        r2 = await _post_role(client, cid, "referee", "dup")
        assert r2.status_code == 422
        assert r2.json()["detail"].startswith("role_label_duplicate")

    async def test_restructure_kind_with_label(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        r = await _post_role(client, cid, "restructure", "rewrite")
        assert r.status_code == 201, r.text


@pytest.mark.asyncio(loop_scope="session")
class TestRoutingRulesEndpoint:
    async def _seed_referee(self, client: AsyncClient, cid: int, label: str = "main_judge") -> None:
        r = await _post_role(client, cid, "referee", label)
        assert r.status_code == 201, r.text

    async def test_put_and_get_routing_rules(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        await self._seed_referee(client, cid)
        put = await client.put(
            f"/campaigns/{cid}/routing-rules",
            json={
                "routing_rules": [GOAL_RULE],
                "max_continuous_restructure": 3,
            },
        )
        assert put.status_code == 200, put.text
        got = (await client.get(f"/campaigns/{cid}/routing-rules")).json()
        assert len(got["routing_rules"]) == 1
        assert got["max_continuous_restructure"] == 3

    async def test_routing_rule_unknown_referee_rejected(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        await self._seed_referee(client, cid)
        bad = {**GOAL_RULE, "referee": "nonexistent"}
        r = await client.put(f"/campaigns/{cid}/routing-rules", json={"routing_rules": [bad]})
        assert r.status_code == 422
        assert r.json()["detail"].startswith("routing_rule_unknown_referee")

    async def test_goal_achieved_without_goal_type_rejected(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        await self._seed_referee(client, cid)
        bad_rule = {
            "referee": "main_judge",
            "match": ["goal_achieved"],
            "action": {"type": "transition", "to": "goal_achieved"},  # missing goal_type
        }
        r = await client.put(f"/campaigns/{cid}/routing-rules", json={"routing_rules": [bad_rule]})
        assert r.status_code == 422  # RoutingRule schema validator



@pytest.mark.asyncio(loop_scope="session")
class TestRefereeDeleteProtection:
    async def test_delete_referee_in_use_rejected(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        created = await _post_role(client, cid, "referee", "main_judge")
        rid = created.json()["id"]
        put = await client.put(
            f"/campaigns/{cid}/routing-rules", json={"routing_rules": [GOAL_RULE]}
        )
        assert put.status_code == 200, put.text
        # Now the referee is referenced by a routing rule → delete blocked.
        deleted = await client.delete(f"/role-configs/{rid}")
        assert deleted.status_code == 409
        assert deleted.json()["detail"].startswith("referee_label_in_use")

    async def test_delete_unreferenced_referee_ok(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid = await _seed_campaign(clean_engine)
        created = await _post_role(client, cid, "referee", "spare")
        rid = created.json()["id"]
        deleted = await client.delete(f"/role-configs/{rid}")
        assert deleted.status_code == 204
