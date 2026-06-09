"""/filler-phrases CRUD — a campaign's flat filler pool.

Spec: filler § 垫词池随机不重复; web-admin-ui § per-campaign 外呼策略配置.

The ``filler_set`` grouping layer was removed in ``filler-single-pool``;
``filler_phrase`` rows now hang off ``campaign`` directly and are managed as a
single flat list per campaign (no naming / sort_order / round-robin).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.models import Campaign, FillerPhrase
from isales_common.schemas.filler import (
    FillerPhraseCreate,
    FillerPhraseRead,
    FillerPhraseUpdate,
)
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/filler-phrases", tags=["filler-phrases"])


@router.get("", response_model=Page[FillerPhraseRead])
async def list_filler_phrases(
    session: DBSession,
    _user: CurrentUser,
    campaign_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[FillerPhraseRead]:
    stmt = select(FillerPhrase).order_by(FillerPhrase.id)
    count_stmt = select(func.count()).select_from(FillerPhrase)
    if campaign_id is not None:
        stmt = stmt.where(FillerPhrase.campaign_id == campaign_id)
        count_stmt = count_stmt.where(FillerPhrase.campaign_id == campaign_id)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[FillerPhraseRead](
        items=[FillerPhraseRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=FillerPhraseRead, status_code=status.HTTP_201_CREATED)
async def create_filler_phrase(
    payload: FillerPhraseCreate, session: DBSession, _user: CurrentUser
) -> FillerPhraseRead:
    if (await session.get(Campaign, payload.campaign_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    obj = FillerPhrase(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return FillerPhraseRead.model_validate(obj)


@router.patch("/{phrase_id}", response_model=FillerPhraseRead)
async def update_filler_phrase(
    phrase_id: int,
    payload: FillerPhraseUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> FillerPhraseRead:
    obj = await session.get(FillerPhrase, phrase_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="filler_phrase_not_found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await session.refresh(obj)
    return FillerPhraseRead.model_validate(obj)


@router.delete("/{phrase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filler_phrase(
    phrase_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(FillerPhrase, phrase_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="filler_phrase_not_found")
    await session.delete(obj)
