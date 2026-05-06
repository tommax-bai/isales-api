"""/campaigns CRUD with nested children + /campaigns/{id}/devices association.

Plus /campaigns/{id}/start | /pause — both write a CampaignControl message
(StartCampaign / PauseCampaign) to Redis list ``scheduler:campaign-control``
which scheduler (stage 3) will consume.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from isales_common.models import (
    CallbackConfig,
    Campaign,
    CampaignDevice,
    FillerPhrase,
    FillerSet,
    RoleConfig,
)
from isales_common.schemas.callback import CallbackConfigRead
from isales_common.schemas.messages import PauseCampaign, StartCampaign
from isales_common.schemas.role_config import RoleConfigRead
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.common.redis import get_redis
from isales_api.schemas import (
    CallbackConfigNestedWrite,
    CampaignDetailRead,
    CampaignDeviceAttach,
    CampaignDeviceRead,
    CampaignNestedCreate,
    CampaignNestedUpdate,
    FillerPhraseRead,
    FillerSetNestedWrite,
    FillerSetWithPhrasesRead,
    Page,
    RoleConfigNestedWrite,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


_CAMPAIGN_NESTED_LOAD = (
    selectinload(Campaign.role_configs) if False else None
)
# selectinload requires relationships to exist on the model. They aren't
# declared on Campaign in isales-common, so we load children with explicit
# follow-up queries below — keeps this PR scoped.


async def _load_detail(session: DBSession, campaign_id: int) -> CampaignDetailRead | None:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        return None
    role_rows = (
        await session.execute(
            select(RoleConfig).where(RoleConfig.campaign_id == campaign_id)
        )
    ).scalars().all()
    filler_set_rows = (
        await session.execute(
            select(FillerSet).where(FillerSet.campaign_id == campaign_id)
        )
    ).scalars().all()
    filler_set_ids = [fs.id for fs in filler_set_rows]
    phrases_by_set: dict[int, list[FillerPhrase]] = {fs_id: [] for fs_id in filler_set_ids}
    if filler_set_ids:
        phrase_rows = (
            await session.execute(
                select(FillerPhrase).where(FillerPhrase.filler_set_id.in_(filler_set_ids))
            )
        ).scalars().all()
        for p in phrase_rows:
            phrases_by_set.setdefault(p.filler_set_id, []).append(p)
    callback_rows = (
        await session.execute(
            select(CallbackConfig).where(CallbackConfig.campaign_id == campaign_id)
        )
    ).scalars().all()

    return CampaignDetailRead(
        **{c.name: getattr(campaign, c.name) for c in Campaign.__table__.columns},
        role_configs=[RoleConfigRead.model_validate(r) for r in role_rows],
        filler_sets=[
            FillerSetWithPhrasesRead(
                **{c.name: getattr(fs, c.name) for c in FillerSet.__table__.columns},
                phrases=[FillerPhraseRead.model_validate(p) for p in phrases_by_set.get(fs.id, [])],
            )
            for fs in filler_set_rows
        ],
        callback_configs=[CallbackConfigRead.model_validate(c) for c in callback_rows],
    )


def _campaign_base_fields(payload: CampaignNestedCreate) -> dict[str, object]:
    """Pluck all CampaignBase fields (excluding the nested children)."""
    excluded = {"role_configs", "filler_sets", "callback_configs"}
    return payload.model_dump(exclude=excluded)


async def _replace_children(
    session: DBSession,
    campaign_id: int,
    *,
    role_configs: list[RoleConfigNestedWrite] | None,
    filler_sets: list[FillerSetNestedWrite] | None,
    callback_configs: list[CallbackConfigNestedWrite] | None,
) -> None:
    if role_configs is not None:
        await session.execute(
            delete(RoleConfig).where(RoleConfig.campaign_id == campaign_id)
        )
        for rc in role_configs:
            session.add(RoleConfig(campaign_id=campaign_id, **rc.model_dump()))

    if filler_sets is not None:
        # CASCADE on filler_set → filler_phrase, so deleting the set is enough.
        await session.execute(
            delete(FillerSet).where(FillerSet.campaign_id == campaign_id)
        )
        for fs in filler_sets:
            phrases_payload = fs.phrases
            new_set = FillerSet(
                campaign_id=campaign_id,
                **fs.model_dump(exclude={"phrases"}),
            )
            session.add(new_set)
            await session.flush()
            for ph in phrases_payload:
                session.add(FillerPhrase(filler_set_id=new_set.id, **ph.model_dump()))

    if callback_configs is not None:
        await session.execute(
            delete(CallbackConfig).where(CallbackConfig.campaign_id == campaign_id)
        )
        for cb in callback_configs:
            session.add(CallbackConfig(campaign_id=campaign_id, **cb.model_dump()))


# ─── routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=Page[CampaignDetailRead])
async def list_campaigns(
    session: DBSession,
    _user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[CampaignDetailRead]:
    total = (await session.execute(select(func.count()).select_from(Campaign))).scalar_one()
    rows = (
        await session.execute(
            select(Campaign)
            .order_by(Campaign.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    items: list[CampaignDetailRead] = []
    for c in rows:
        detail = await _load_detail(session, c.id)
        if detail is not None:
            items.append(detail)
    return Page[CampaignDetailRead](
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{campaign_id}", response_model=CampaignDetailRead)
async def get_campaign(
    campaign_id: int, session: DBSession, _user: CurrentUser
) -> CampaignDetailRead:
    detail = await _load_detail(session, campaign_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    return detail


@router.post("", response_model=CampaignDetailRead, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignNestedCreate, session: DBSession, _user: CurrentUser
) -> CampaignDetailRead:
    campaign = Campaign(**_campaign_base_fields(payload))
    session.add(campaign)
    await session.flush()
    await _replace_children(
        session,
        campaign.id,
        role_configs=payload.role_configs,
        filler_sets=payload.filler_sets,
        callback_configs=payload.callback_configs,
    )
    await session.flush()
    detail = await _load_detail(session, campaign.id)
    assert detail is not None
    return detail


@router.patch("/{campaign_id}", response_model=CampaignDetailRead)
async def update_campaign(
    campaign_id: int,
    payload: CampaignNestedUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> CampaignDetailRead:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    base_fields = payload.model_dump(
        exclude_unset=True,
        exclude={"role_configs", "filler_sets", "callback_configs"},
    )
    for k, v in base_fields.items():
        setattr(campaign, k, v)
    await _replace_children(
        session,
        campaign_id,
        role_configs=payload.role_configs,
        filler_sets=payload.filler_sets,
        callback_configs=payload.callback_configs,
    )
    await session.flush()
    detail = await _load_detail(session, campaign_id)
    assert detail is not None
    return detail


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: int, session: DBSession, _user: CurrentUser
) -> None:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    await session.delete(campaign)


# ─── /campaigns/{id}/devices ─────────────────────────────────────────────────


@router.get("/{campaign_id}/devices", response_model=list[CampaignDeviceRead])
async def list_campaign_devices(
    campaign_id: int, session: DBSession, _user: CurrentUser
) -> list[CampaignDeviceRead]:
    rows = (
        await session.execute(
            select(CampaignDevice)
            .where(CampaignDevice.campaign_id == campaign_id)
            .order_by(CampaignDevice.id)
        )
    ).scalars().all()
    return [CampaignDeviceRead.model_validate(r) for r in rows]


@router.post(
    "/{campaign_id}/devices",
    response_model=CampaignDeviceRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_campaign_device(
    campaign_id: int,
    payload: CampaignDeviceAttach,
    session: DBSession,
    _user: CurrentUser,
) -> CampaignDeviceRead:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    row = CampaignDevice(campaign_id=campaign_id, device_id=payload.device_id)
    session.add(row)
    await session.flush()
    return CampaignDeviceRead.model_validate(row)


@router.delete(
    "/{campaign_id}/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def detach_campaign_device(
    campaign_id: int,
    device_id: int,
    session: DBSession,
    _user: CurrentUser,
) -> None:
    result = await session.execute(
        delete(CampaignDevice).where(
            (CampaignDevice.campaign_id == campaign_id)
            & (CampaignDevice.device_id == device_id)
        )
    )
    if (result.rowcount or 0) == 0:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="binding_not_found")


# ─── /campaigns/{id}/start | /pause → scheduler queue ────────────────────────


CAMPAIGN_CONTROL_QUEUE = "scheduler:campaign-control"


async def _enqueue_control(request: Request, message: StartCampaign | PauseCampaign) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        redis = get_redis()
        request.app.state.redis = redis
    await redis.lpush(CAMPAIGN_CONTROL_QUEUE, message.model_dump_json())


@router.post("/{campaign_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_campaign(
    campaign_id: int,
    request: Request,
    session: DBSession,
    _user: CurrentUser,
) -> dict[str, object]:
    if (await session.get(Campaign, campaign_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    msg = StartCampaign(campaign_id=campaign_id)
    await _enqueue_control(request, msg)
    return {"message_id": str(msg.message_id), "queued": True}


@router.post("/{campaign_id}/pause", status_code=status.HTTP_202_ACCEPTED)
async def pause_campaign(
    campaign_id: int,
    request: Request,
    session: DBSession,
    _user: CurrentUser,
) -> dict[str, object]:
    if (await session.get(Campaign, campaign_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    msg = PauseCampaign(campaign_id=campaign_id)
    await _enqueue_control(request, msg)
    return {"message_id": str(msg.message_id), "queued": True}
