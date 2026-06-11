"""/role-configs CRUD — per-campaign LLM slot config (main / referee / extractor / restructure).

Spec: openspec/changes/archive/2026-06-05-engine-multi-referee-and-restructure —
capability `web-admin-ui`. A campaign has exactly one main / one extractor slot,
an optional single restructure slot, and **N referee slots** (was a single
referee pre-multi-referee). ``referee`` rows carry a ``label`` (unique per
campaign) that ``campaign.routing_rules`` bind to; deleting a still-referenced
referee is rejected (409). (per-role-llm-config-and-restructure-card: the
singleton ``restructure`` slot is routed via the builtin route, not by label, so
it carries no required label.) ``kind`` is validated against the
``RoleKind`` enum, so the old ``role`` / ``judge`` / ``polish`` values are
rejected automatically.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.enums import RoleKind
from isales_common.models import Campaign, RoleConfig
from isales_common.schemas.jsonb import RoutePersonaAction, RoutingRule
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

# persona labels are isolated; referee + restructure share one namespace
# (engine-tools-multidialogue-gating). A referee "warm" and a persona "warm"
# may coexist.
_PERSONA_KINDS = {RoleKind.PERSONA.value}
# per-role-llm-config-and-restructure-card: RESTRUCTURE dropped from labelled kinds
# — it is a singleton slot reached via the builtin ``restructure`` route (not by
# label), so the standalone CRUD MUST NOT require a label for it (mirrors
# routing_validation._LABELLED_KINDS).
_REFEREE_KINDS = {RoleKind.REFEREE.value}
_LABELLED_KINDS = _PERSONA_KINDS | _REFEREE_KINDS


async def _require_unique_label(
    session: DBSession,
    campaign_id: int,
    kind: str,
    label: str | None,
    *,
    exclude_id: int | None = None,
) -> None:
    """referee/persona rows MUST carry a non-empty label, unique per campaign
    WITHIN their namespace (persona isolated from referee). restructure is a
    singleton routed via the builtin route, so it requires no label."""
    if kind not in _LABELLED_KINDS:
        return
    if not (label and label.strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="role_label_required"
        )
    namespace = _PERSONA_KINDS if kind in _PERSONA_KINDS else _REFEREE_KINDS
    stmt = select(func.count()).select_from(RoleConfig).where(
        RoleConfig.campaign_id == campaign_id,
        RoleConfig.label == label,
        RoleConfig.kind.in_(namespace),
    )
    if exclude_id is not None:
        stmt = stmt.where(RoleConfig.id != exclude_id)
    if (await session.execute(stmt)).scalar_one() > 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"role_label_duplicate:{label}"
        )


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
    await _require_unique_label(
        session, payload.campaign_id, payload.kind.value, payload.label
    )
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
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    # Re-validate label uniqueness against the post-patch kind/label.
    await _require_unique_label(
        session,
        obj.campaign_id,
        obj.kind.value if hasattr(obj.kind, "value") else obj.kind,
        obj.label,
        exclude_id=obj.id,
    )
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
    # A referee can't be deleted while routing_rules still reference its label
    # (§5.3) — that would leave dangling references.
    kind = obj.kind.value if hasattr(obj.kind, "value") else obj.kind
    if kind == RoleKind.REFEREE.value and obj.label:
        campaign = await session.get(Campaign, obj.campaign_id)
        if campaign is not None:
            referencing = [
                RoutingRule.model_validate(r).referee
                for r in (campaign.routing_rules or [])
            ]
            if obj.label in referencing:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=f"referee_label_in_use:{obj.label}",
                )
    # A persona can't be deleted while a route action still targets its label
    # (engine-tools-multidialogue-gating) — that would leave a dangling route.
    if kind == RoleKind.PERSONA.value and obj.label:
        campaign = await session.get(Campaign, obj.campaign_id)
        if campaign is not None:
            for r in (campaign.routing_rules or []):
                action = RoutingRule.model_validate(r).action
                if isinstance(action, RoutePersonaAction) and action.to == obj.label:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail=f"persona_label_in_use:{obj.label}",
                    )
    await session.delete(obj)
