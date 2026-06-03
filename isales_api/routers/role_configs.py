"""/role-configs CRUD — per-campaign LLM slot config (main / referee / extractor).

Spec: openspec/changes/pipeline-stream-and-referee — capability `web-admin-ui`.
The dual-LLM architecture has exactly one main / one referee / one extractor
slot per campaign (no N-role / M-judge PK). ``kind`` is validated against the
``RoleKind`` enum, so the old ``role`` / ``judge`` / ``polish`` values are
rejected automatically.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.enums import RoleKind
from isales_common.models import RoleConfig
from isales_common.schemas.role_config import (
    RoleConfigCreate,
    RoleConfigRead,
    RoleConfigUpdate,
)
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/role-configs", tags=["role-configs"])


@router.get("", response_model=Page[RoleConfigRead])
async def list_role_configs(
    session: DBSession,
    _user: CurrentUser,
    campaign_id: Annotated[int | None, Query()] = None,
    kind: Annotated[RoleKind | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[RoleConfigRead]:
    stmt = select(RoleConfig).order_by(RoleConfig.id)
    count_stmt = select(func.count()).select_from(RoleConfig)
    if campaign_id is not None:
        stmt = stmt.where(RoleConfig.campaign_id == campaign_id)
        count_stmt = count_stmt.where(RoleConfig.campaign_id == campaign_id)
    if kind is not None:
        stmt = stmt.where(RoleConfig.kind == kind)
        count_stmt = count_stmt.where(RoleConfig.kind == kind)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[RoleConfigRead](
        items=[RoleConfigRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{role_config_id}", response_model=RoleConfigRead)
async def get_role_config(
    role_config_id: int, session: DBSession, _user: CurrentUser
) -> RoleConfigRead:
    obj = await session.get(RoleConfig, role_config_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="role_config_not_found")
    return RoleConfigRead.model_validate(obj)


@router.post("", response_model=RoleConfigRead, status_code=status.HTTP_201_CREATED)
async def create_role_config(
    payload: RoleConfigCreate, session: DBSession, _user: CurrentUser
) -> RoleConfigRead:
    obj = RoleConfig(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return RoleConfigRead.model_validate(obj)


@router.patch("/{role_config_id}", response_model=RoleConfigRead)
async def update_role_config(
    role_config_id: int,
    payload: RoleConfigUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> RoleConfigRead:
    obj = await session.get(RoleConfig, role_config_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="role_config_not_found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await session.refresh(obj)
    return RoleConfigRead.model_validate(obj)


@router.delete("/{role_config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role_config(
    role_config_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(RoleConfig, role_config_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="role_config_not_found")
    await session.delete(obj)
