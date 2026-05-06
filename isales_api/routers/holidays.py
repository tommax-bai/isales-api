"""/holidays CRUD."""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.models import Holiday
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import HolidayCreate, HolidayRead, HolidayUpdate, Page

router = APIRouter(prefix="/holidays", tags=["holidays"])


@router.get("", response_model=Page[HolidayRead])
async def list_holidays(
    session: DBSession,
    _user: CurrentUser,
    region: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Page[HolidayRead]:
    stmt = select(Holiday).order_by(Holiday.date)
    count_stmt = select(func.count()).select_from(Holiday)
    if region is not None:
        stmt = stmt.where(Holiday.region == region)
        count_stmt = count_stmt.where(Holiday.region == region)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[HolidayRead](
        items=[
            HolidayRead(
                id=h.id,
                date=h.date.isoformat(),
                name=h.name,
                region=h.region,
                created_at=h.created_at,
                updated_at=h.updated_at,
            )
            for h in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=HolidayRead, status_code=status.HTTP_201_CREATED)
async def create_holiday(
    payload: HolidayCreate, session: DBSession, _user: CurrentUser
) -> HolidayRead:
    try:
        d = date_type.fromisoformat(payload.date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid_date: {exc}",
        ) from exc
    obj = Holiday(date=d, name=payload.name, region=payload.region)
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return HolidayRead(
        id=obj.id,
        date=obj.date.isoformat(),
        name=obj.name,
        region=obj.region,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


@router.patch("/{holiday_id}", response_model=HolidayRead)
async def update_holiday(
    holiday_id: int,
    payload: HolidayUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> HolidayRead:
    obj = await session.get(Holiday, holiday_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="holiday_not_found")
    data = payload.model_dump(exclude_unset=True)
    if "date" in data:
        try:
            obj.date = date_type.fromisoformat(data.pop("date"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid_date: {exc}",
            ) from exc
    for k, v in data.items():
        setattr(obj, k, v)
    await session.flush()
    await session.refresh(obj)
    return HolidayRead(
        id=obj.id,
        date=obj.date.isoformat(),
        name=obj.name,
        region=obj.region,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


@router.delete("/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    holiday_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(Holiday, holiday_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="holiday_not_found")
    await session.delete(obj)
