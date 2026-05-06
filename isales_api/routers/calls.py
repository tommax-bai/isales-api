"""/calls read-only — list (filter+page) and detail (with transcript/summary)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.enums import CallStatus
from isales_common.models import CallRecord, CallSummary
from isales_common.schemas.call import CallRecordRead, CallSummaryRead
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("", response_model=Page[CallRecordRead])
async def list_calls(
    session: DBSession,
    _user: CurrentUser,
    campaign_id: Annotated[int | None, Query()] = None,
    lead_id: Annotated[int | None, Query()] = None,
    status_filter: Annotated[CallStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[CallRecordRead]:
    stmt = select(CallRecord).order_by(CallRecord.id.desc())
    count_stmt = select(func.count()).select_from(CallRecord)
    if campaign_id is not None:
        stmt = stmt.where(CallRecord.campaign_id == campaign_id)
        count_stmt = count_stmt.where(CallRecord.campaign_id == campaign_id)
    if lead_id is not None:
        stmt = stmt.where(CallRecord.lead_id == lead_id)
        count_stmt = count_stmt.where(CallRecord.lead_id == lead_id)
    if status_filter is not None:
        stmt = stmt.where(CallRecord.status == status_filter)
        count_stmt = count_stmt.where(CallRecord.status == status_filter)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[CallRecordRead](
        items=[CallRecordRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{call_id}", response_model=CallRecordRead)
async def get_call(
    call_id: int, session: DBSession, _user: CurrentUser
) -> CallRecordRead:
    obj = await session.get(CallRecord, call_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="call_not_found")
    return CallRecordRead.model_validate(obj)


@router.get("/{call_id}/summary", response_model=CallSummaryRead)
async def get_call_summary(
    call_id: int, session: DBSession, _user: CurrentUser
) -> CallSummaryRead:
    summary = (
        await session.execute(
            select(CallSummary).where(CallSummary.call_record_id == call_id)
        )
    ).scalar_one_or_none()
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="summary_not_found")
    return CallSummaryRead.model_validate(summary)
