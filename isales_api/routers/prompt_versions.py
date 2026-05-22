"""/prompt-versions CRUD — versioned LLM prompt content.

Spec: openspec/changes/web-admin-campaign-workflow — capability `web-admin-ui`
§ "per-campaign 外呼策略配置"; role-prompt for assembly rules.

A `prompt_version` is scoped by (scope_type, scope_id). At most one version
per scope SHALL be active — setting `is_active=true` on one deactivates the
rest in the same scope (same DB transaction).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.enums import PromptScopeType
from isales_common.models import PromptVersion
from isales_common.schemas.prompt import (
    PromptVersionCreate,
    PromptVersionRead,
    PromptVersionUpdate,
)
from sqlalchemy import func, select, update

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/prompt-versions", tags=["prompt-versions"])


async def _deactivate_siblings(
    session: DBSession, *, scope_type: PromptScopeType, scope_id: int, keep_id: int
) -> None:
    """Set is_active=false on every other version in the same scope."""
    await session.execute(
        update(PromptVersion)
        .where(PromptVersion.scope_type == scope_type)
        .where(PromptVersion.scope_id == scope_id)
        .where(PromptVersion.id != keep_id)
        .values(is_active=False)
    )


@router.get("", response_model=Page[PromptVersionRead])
async def list_prompt_versions(
    session: DBSession,
    _user: CurrentUser,
    scope_type: Annotated[PromptScopeType | None, Query()] = None,
    scope_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[PromptVersionRead]:
    stmt = select(PromptVersion).order_by(PromptVersion.id.desc())
    count_stmt = select(func.count()).select_from(PromptVersion)
    if scope_type is not None:
        stmt = stmt.where(PromptVersion.scope_type == scope_type)
        count_stmt = count_stmt.where(PromptVersion.scope_type == scope_type)
    if scope_id is not None:
        stmt = stmt.where(PromptVersion.scope_id == scope_id)
        count_stmt = count_stmt.where(PromptVersion.scope_id == scope_id)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[PromptVersionRead](
        items=[PromptVersionRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{prompt_version_id}", response_model=PromptVersionRead)
async def get_prompt_version(
    prompt_version_id: int, session: DBSession, _user: CurrentUser
) -> PromptVersionRead:
    obj = await session.get(PromptVersion, prompt_version_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="prompt_version_not_found")
    return PromptVersionRead.model_validate(obj)


@router.post("", response_model=PromptVersionRead, status_code=status.HTTP_201_CREATED)
async def create_prompt_version(
    payload: PromptVersionCreate, session: DBSession, _user: CurrentUser
) -> PromptVersionRead:
    obj = PromptVersion(**payload.model_dump())
    session.add(obj)
    await session.flush()
    if obj.is_active:
        await _deactivate_siblings(
            session, scope_type=obj.scope_type, scope_id=obj.scope_id, keep_id=obj.id
        )
    await session.refresh(obj)
    return PromptVersionRead.model_validate(obj)


@router.patch("/{prompt_version_id}", response_model=PromptVersionRead)
async def update_prompt_version(
    prompt_version_id: int,
    payload: PromptVersionUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> PromptVersionRead:
    obj = await session.get(PromptVersion, prompt_version_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="prompt_version_not_found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    if obj.is_active:
        await _deactivate_siblings(
            session, scope_type=obj.scope_type, scope_id=obj.scope_id, keep_id=obj.id
        )
    await session.refresh(obj)
    return PromptVersionRead.model_validate(obj)


@router.delete("/{prompt_version_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_version(
    prompt_version_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(PromptVersion, prompt_version_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="prompt_version_not_found")
    await session.delete(obj)
