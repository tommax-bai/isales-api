"""Tests for /calls (read-only) + /analytics aggregations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from isales_common.models import CallRecord, CallSummary, Campaign, Lead
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


async def _seed(engine: AsyncEngine) -> tuple[int, list[int]]:
    """Seed: 1 campaign, 1 lead, 5 calls (3 answered w/ durations 20/100/700,
    2 unanswered). 2 of the answered ones have summaries; goal_achieved on 1.
    Returns (campaign_id, [call_ids])."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        c = Campaign(name="A")
        session.add(c)
        await session.flush()
        lead = Lead(campaign_id=c.id, phone="+861380000")
        session.add(lead)
        await session.flush()

        now = datetime.now(tz=UTC)
        rows = []
        for started_at, duration in [
            (now, 20),
            (now, 100),
            (now, 700),
            (None, None),
            (None, None),
        ]:
            cr = CallRecord(
                lead_id=lead.id,
                campaign_id=c.id,
                started_at=started_at,
                duration=duration,
            )
            session.add(cr)
            rows.append(cr)
        await session.flush()
        # summaries: 2 connected, 1 with goal achieved
        for cr, achieved in [(rows[0], True), (rows[1], False)]:
            session.add(
                CallSummary(call_record_id=cr.id, summary_text="x", goal_achieved=achieved)
            )
        await session.commit()
        return c.id, [r.id for r in rows]


@pytest.mark.asyncio(loop_scope="session")
class TestCalls:
    async def test_list_filter_pagination(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        cid, _call_ids = await _seed(clean_engine)
        body = (await client.get("/calls", params={"campaign_id": cid})).json()
        assert body["total"] == 5

        page1 = (await client.get("/calls", params={"page_size": 2})).json()
        assert len(page1["items"]) == 2

    async def test_get_detail_404(self, client: AsyncClient) -> None:
        assert (await client.get("/calls/9999")).status_code == 404

    async def test_summary_404_when_absent(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        _cid, call_ids = await _seed(clean_engine)
        # Call ids[2] has no summary in seed.
        resp = await client.get(f"/calls/{call_ids[2]}/summary")
        assert resp.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
class TestTranscriptSchemaContract:
    """Regression for fix-transcript-schema-drift: GET /calls validates every
    record's transcript with CallRecordRead (extra="forbid"). A record whose
    transcript carries a state_warning event MUST read back 200; an ai_reply
    event MUST NOT carry an `interrupted` field (engine no longer writes it).
    """

    async def test_list_reads_transcript_with_state_warning(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        sm = async_sessionmaker(clean_engine, expire_on_commit=False)
        async with sm() as session:
            c = Campaign(name="T")
            session.add(c)
            await session.flush()
            lead = Lead(campaign_id=c.id, phone="+861380001")
            session.add(lead)
            await session.flush()
            session.add(
                CallRecord(
                    lead_id=lead.id,
                    campaign_id=c.id,
                    started_at=datetime.now(tz=UTC),
                    duration=42,
                    transcript=[
                        {"type": "greeting", "ts": 0, "text": "您好"},
                        {"type": "user_speech", "ts": 100, "text": "你好"},
                        {
                            "type": "ai_reply",
                            "ts": 200,
                            "text": "好的",
                            "turn_id": 1,
                        },
                        {
                            "type": "state_warning",
                            "ts": 300,
                            "attempted": "bug",
                            "from_state": "in_call",
                            "to_state": "transferring",
                        },
                        {"type": "hangup", "ts": 400, "reason": "user_hangup",
                         "initiated_by": "user"},
                    ],
                )
            )
            await session.commit()

        resp = await client.get("/calls", params={"campaign_id": c.id})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        types = [e["type"] for e in items[0]["transcript"]]
        assert "state_warning" in types
        ai = next(e for e in items[0]["transcript"] if e["type"] == "ai_reply")
        assert "interrupted" not in ai

    async def test_ai_reply_with_interrupted_is_rejected(self) -> None:
        from isales_common.schemas.call import CallRecordRead
        from pydantic import ValidationError

        now = datetime.now(tz=UTC)
        base = {
            "id": 1, "lead_id": 1, "campaign_id": 1, "status": "end",
            "transfer_status": "none", "created_at": now, "updated_at": now,
            "transcript": [
                {"type": "ai_reply", "ts": 1, "text": "x", "turn_id": 1,
                 "interrupted": False},
            ],
        }
        with pytest.raises(ValidationError) as exc:
            CallRecordRead.model_validate(base)
        # The only contract violation is the extra `interrupted` key.
        assert "interrupted" in str(exc.value)


@pytest.mark.asyncio(loop_scope="session")
class TestAnalytics:
    async def test_answer_rate(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        await _seed(clean_engine)
        body = (await client.get("/analytics/answer-rate")).json()
        assert body["total"] == 5
        assert body["answered"] == 3
        assert abs(body["rate"] - 0.6) < 1e-9

    async def test_goal_rate(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        await _seed(clean_engine)
        body = (await client.get("/analytics/goal-rate")).json()
        # 5 calls; 1 summary has goal_achieved
        assert body["total_calls"] == 5
        assert body["goal_achieved"] == 1
        assert abs(body["rate"] - 0.2) < 1e-9

    async def test_duration_distribution(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        await _seed(clean_engine)
        body = (await client.get("/analytics/duration-distribution")).json()
        assert body["total"] == 3
        # 20 → 0-30s, 100 → 1-3m, 700 → >10m
        by_label = {b["label"]: b["count"] for b in body["buckets"]}
        assert by_label["0-30s"] == 1
        assert by_label["1-3m"] == 1
        assert by_label[">10m"] == 1
        assert by_label["30-60s"] == 0

    async def test_window_excludes_old_records(
        self, client: AsyncClient, clean_engine: AsyncEngine
    ) -> None:
        await _seed(clean_engine)
        old_until = (datetime.now(tz=UTC) - timedelta(days=8)).isoformat()
        body = (
            await client.get("/analytics/answer-rate", params={"until": old_until})
        ).json()
        assert body["total"] == 0
