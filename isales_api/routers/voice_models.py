"""/voice-models CRUD + /sample stream."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from isales_common.models import VoiceModel
from isales_common.schemas.voice_model import (
    VoiceModelCreate,
    VoiceModelRead,
    VoiceModelUpdate,
)
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/voice-models", tags=["voice-models"])


@router.get("", response_model=Page[VoiceModelRead])
async def list_voice_models(
    session: DBSession,
    _user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[VoiceModelRead]:
    total = (await session.execute(select(func.count()).select_from(VoiceModel))).scalar_one()
    rows = (
        await session.execute(
            select(VoiceModel)
            .order_by(VoiceModel.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[VoiceModelRead](
        items=[VoiceModelRead.model_validate(v) for v in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{voice_id}", response_model=VoiceModelRead)
async def get_voice_model(
    voice_id: int, session: DBSession, _user: CurrentUser
) -> VoiceModelRead:
    obj = await session.get(VoiceModel, voice_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="voice_model_not_found")
    return VoiceModelRead.model_validate(obj)


@router.post("", response_model=VoiceModelRead, status_code=status.HTTP_201_CREATED)
async def create_voice_model(
    payload: VoiceModelCreate, session: DBSession, _user: CurrentUser
) -> VoiceModelRead:
    obj = VoiceModel(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return VoiceModelRead.model_validate(obj)


@router.patch("/{voice_id}", response_model=VoiceModelRead)
async def update_voice_model(
    voice_id: int,
    payload: VoiceModelUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> VoiceModelRead:
    obj = await session.get(VoiceModel, voice_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="voice_model_not_found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await session.refresh(obj)
    return VoiceModelRead.model_validate(obj)


@router.delete("/{voice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice_model(
    voice_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(VoiceModel, voice_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="voice_model_not_found")
    await session.delete(obj)


@router.get("/{voice_id}/sample", response_class=RedirectResponse)
async def get_voice_sample(
    voice_id: int, session: DBSession, _user: CurrentUser
) -> RedirectResponse:
    """Return audio for the voice's sample.

    v1 stage 2: redirect to ``sample_url``. The frontend can stream the audio
    directly from object storage. Stage 6/7 may swap this for an in-process
    proxy if CORS / auth on the storage tier becomes an issue.
    """
    obj = await session.get(VoiceModel, voice_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="voice_model_not_found")
    if not obj.sample_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="sample_not_available"
        )
    return RedirectResponse(url=obj.sample_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
