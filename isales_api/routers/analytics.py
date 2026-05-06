"""/analytics aggregate endpoints (SQL group-by; no pre-aggregation in v1).

All three endpoints take optional ``?since=`` and ``?until=`` ISO datetimes;
default window is the last 7 days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from isales_common.models import CallRecord, CallSummary
from pydantic import BaseModel
from sqlalchemy import case, func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnswerRate(BaseModel):
    total: int
    answered: int
    rate: float  # 0..1


class GoalRate(BaseModel):
    total_calls: int
    goal_achieved: int
    rate: float


class DurationBucket(BaseModel):
    label: str
    lower_seconds: int
    upper_seconds: int | None  # None == open upper
    count: int


class DurationDistribution(BaseModel):
    total: int
    buckets: list[DurationBucket]


def _window(
    since: datetime | None, until: datetime | None
) -> tuple[datetime, datetime]:
    end = until or datetime.now(tz=UTC)
    start = since or (end - timedelta(days=7))
    return start, end


@router.get("/answer-rate", response_model=AnswerRate)
async def answer_rate(
    session: DBSession,
    _user: CurrentUser,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    campaign_id: Annotated[int | None, Query()] = None,
) -> AnswerRate:
    start, end = _window(since, until)
    # "Answered" == call connected to the callee, captured by started_at being
    # set. CallStatus values are stages of a connected call; an INIT-only row
    # never reached the callee.
    stmt = select(
        func.count().label("total"),
        func.coalesce(
            func.sum(case((CallRecord.started_at.isnot(None), 1), else_=0)), 0
        ).label("answered"),
    ).where(CallRecord.created_at >= start, CallRecord.created_at < end)
    if campaign_id is not None:
        stmt = stmt.where(CallRecord.campaign_id == campaign_id)
    row = (await session.execute(stmt)).one()
    total, answered = int(row.total), int(row.answered)
    return AnswerRate(
        total=total, answered=answered, rate=(answered / total) if total else 0.0
    )


@router.get("/goal-rate", response_model=GoalRate)
async def goal_rate(
    session: DBSession,
    _user: CurrentUser,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    campaign_id: Annotated[int | None, Query()] = None,
) -> GoalRate:
    start, end = _window(since, until)
    stmt = (
        select(
            func.count().label("total_calls"),
            func.coalesce(
                func.sum(case((CallSummary.goal_achieved.is_(True), 1), else_=0)), 0
            ).label("goal_achieved"),
        )
        .select_from(CallRecord)
        .join(CallSummary, CallSummary.call_record_id == CallRecord.id, isouter=True)
        .where(CallRecord.created_at >= start, CallRecord.created_at < end)
    )
    if campaign_id is not None:
        stmt = stmt.where(CallRecord.campaign_id == campaign_id)
    row = (await session.execute(stmt)).one()
    total, goal = int(row.total_calls), int(row.goal_achieved)
    return GoalRate(
        total_calls=total, goal_achieved=goal, rate=(goal / total) if total else 0.0
    )


_DURATION_BUCKETS: list[tuple[str, int, int | None]] = [
    ("0-30s", 0, 30),
    ("30-60s", 30, 60),
    ("1-3m", 60, 180),
    ("3-10m", 180, 600),
    (">10m", 600, None),
]


@router.get("/duration-distribution", response_model=DurationDistribution)
async def duration_distribution(
    session: DBSession,
    _user: CurrentUser,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    campaign_id: Annotated[int | None, Query()] = None,
) -> DurationDistribution:
    start, end = _window(since, until)
    base = select(CallRecord.duration).where(
        CallRecord.created_at >= start,
        CallRecord.created_at < end,
        CallRecord.duration.isnot(None),
    )
    if campaign_id is not None:
        base = base.where(CallRecord.campaign_id == campaign_id)
    durations = [int(d or 0) for d in (await session.execute(base)).scalars().all()]

    buckets: list[DurationBucket] = []
    for label, lo, hi in _DURATION_BUCKETS:
        if hi is None:
            count = sum(1 for d in durations if d >= lo)
        else:
            count = sum(1 for d in durations if lo <= d < hi)
        buckets.append(DurationBucket(label=label, lower_seconds=lo, upper_seconds=hi, count=count))
    return DurationDistribution(total=len(durations), buckets=buckets)
