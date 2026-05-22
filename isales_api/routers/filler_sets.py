"""/filler-sets CRUD + nested /filler-sets/{id}/phrases.

Spec: openspec/changes/web-admin-campaign-workflow — capability `web-admin-ui`
§ "per-campaign 外呼策略配置"; filler for selection rules.

`filler_set` is per-campaign; `filler_phrase` rows hang off a set. Phrase
list / create are nested under the set; phrase patch / delete address the
phrase directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.models import FillerPhrase, FillerSet
from isales_common.schemas.filler import (
    FillerPhraseCreate,
    FillerPhraseRead,
    FillerPhraseUpdate,
    FillerSetCreate,
    FillerSetRead,
    FillerSetUpdate,
)
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/filler-sets", tags=["filler-sets"])


# ---- filler_set --------------------------------------------------------


@router.get("", response_model=Page[FillerSetRead])
async def list_filler_sets(
    session: DBSession,
    _user: CurrentUser,
    campaign_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[FillerSetRead]:
    stmt = select(FillerSet).order_by(FillerSet.sort_order, FillerSet.id)
    count_stmt = select(func.count()).select_from(FillerSet)
    if campaign_id is not None:
        stmt = stmt.where(FillerSet.campaign_id == campaign_id)
        count_stmt = count_stmt.where(FillerSet.campaign_id == campaign_id)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[FillerSetRead](
        items=[FillerSetRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=FillerSetRead, status_code=status.HTTP_201_CREATED)
async def create_filler_set(
    payload: FillerSetCreate, session: DBSession, _user: CurrentUser
) -> FillerSetRead:
    obj = FillerSet(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return FillerSetRead.model_validate(obj)


@router.patch("/{filler_set_id}", response_model=FillerSetRead)
async def update_filler_set(
    filler_set_id: int,
    payload: FillerSetUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> FillerSetRead:
    obj = await session.get(FillerSet, filler_set_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="filler_set_not_found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await session.refresh(obj)
    return FillerSetRead.model_validate(obj)


@router.delete("/{filler_set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filler_set(
    filler_set_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(FillerSet, filler_set_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="filler_set_not_found")
    await session.delete(obj)


# ---- filler_phrase (nested) -------------------------------------------


@router.get("/{filler_set_id}/phrases", response_model=list[FillerPhraseRead])
async def list_filler_phrases(
    filler_set_id: int, session: DBSession, _user: CurrentUser
) -> list[FillerPhraseRead]:
    fset = await session.get(FillerSet, filler_set_id)
    if fset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="filler_set_not_found")
    stmt = (
        select(FillerPhrase)
        .where(FillerPhrase.filler_set_id == filler_set_id)
        .order_by(FillerPhrase.id)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [FillerPhraseRead.model_validate(r) for r in rows]


@router.post(
    "/{filler_set_id}/phrases",
    response_model=FillerPhraseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_filler_phrase(
    filler_set_id: int,
    payload: FillerPhraseCreate,
    session: DBSession,
    _user: CurrentUser,
) -> FillerPhraseRead:
    fset = await session.get(FillerSet, filler_set_id)
    if fset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="filler_set_not_found")
    if payload.filler_set_id != filler_set_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="filler_set_id_path_body_mismatch"
        )
    obj = FillerPhrase(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return FillerPhraseRead.model_validate(obj)


@router.patch("/phrases/{phrase_id}", response_model=FillerPhraseRead)
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


@router.delete("/phrases/{phrase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filler_phrase(
    phrase_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(FillerPhrase, phrase_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="filler_phrase_not_found")
    await session.delete(obj)
