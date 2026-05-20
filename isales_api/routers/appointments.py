"""/appointments CRUD + status state-machine.

Spec: openspec/changes/web-admin-ui-redesign — capability `appointment`.

Lifecycle:

    pending ──confirm──> confirmed ──complete──> completed
       │                     │
       └───cancel────┐       └───cancel────┐
                     │                      │
                     └──> cancelled <───────┘

Lead-status side-effects (per spec):
- POST: lead.status → APPOINTED (unless already VISITED / LOST)
- PATCH .../status with action=complete: lead.status → VISITED
- PATCH .../status with action=cancel: lead.status MUST NOT change

All side-effects run in the same DB transaction as the appointment write.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.enums import (
    AppointmentAction,
    AppointmentStatus,
    LeadStatus,
)
from isales_common.models import Appointment, Lead
from isales_common.schemas.appointment import (
    AppointmentCreate,
    AppointmentRead,
    AppointmentStatusAction,
    AppointmentUpdate,
)
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/appointments", tags=["appointments"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Lead statuses that MUST NOT be regressed by a POST /appointments. Once a
# lead is visited or lost, creating a new appointment does not rewind state.
_LEAD_STATUSES_NOT_REGRESSED_TO_APPOINTED = {LeadStatus.VISITED, LeadStatus.LOST}


def _to_read(obj: Appointment) -> AppointmentRead:
    payload = AppointmentRead.model_validate(obj)
    if obj.lead is not None:
        payload.lead_name = obj.lead.name
        payload.lead_phone = obj.lead.phone
    return payload


def _next_status(current: str, action: AppointmentAction) -> AppointmentStatus:
    # SQLAlchemy stores ``status`` as a plain string column; coerce here so
    # the state-machine works regardless of whether we get a StrEnum or a
    # bare ``str`` from the ORM.
    current_enum = AppointmentStatus(current)
    if current_enum in {AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"appointment_terminal:{current_enum.value}",
        )
    if action is AppointmentAction.CONFIRM:
        if current_enum is not AppointmentStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"illegal_transition:{current_enum.value}->confirmed",
            )
        return AppointmentStatus.CONFIRMED
    if action is AppointmentAction.COMPLETE:
        if current_enum is not AppointmentStatus.CONFIRMED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"illegal_transition:{current_enum.value}->completed",
            )
        return AppointmentStatus.COMPLETED
    # action == cancel : allowed from pending or confirmed
    return AppointmentStatus.CANCELLED


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=Page[AppointmentRead])
async def list_appointments(
    session: DBSession,
    _user: CurrentUser,
    status_filter: Annotated[AppointmentStatus | None, Query(alias="status")] = None,
    lead_id: Annotated[int | None, Query()] = None,
    time_from: Annotated[datetime | None, Query()] = None,
    time_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AppointmentRead]:
    stmt = select(Appointment).order_by(Appointment.appointment_time.asc())
    count_stmt = select(func.count()).select_from(Appointment)
    if status_filter is not None:
        stmt = stmt.where(Appointment.status == status_filter)
        count_stmt = count_stmt.where(Appointment.status == status_filter)
    if lead_id is not None:
        stmt = stmt.where(Appointment.lead_id == lead_id)
        count_stmt = count_stmt.where(Appointment.lead_id == lead_id)
    if time_from is not None:
        stmt = stmt.where(Appointment.appointment_time >= time_from)
        count_stmt = count_stmt.where(Appointment.appointment_time >= time_from)
    if time_to is not None:
        stmt = stmt.where(Appointment.appointment_time <= time_to)
        count_stmt = count_stmt.where(Appointment.appointment_time <= time_to)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().unique().all()
    return Page[AppointmentRead](
        items=[_to_read(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{appointment_id}", response_model=AppointmentRead)
async def get_appointment(
    appointment_id: int, session: DBSession, _user: CurrentUser
) -> AppointmentRead:
    obj = await session.get(Appointment, appointment_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="appointment_not_found")
    return _to_read(obj)


@router.post("", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: AppointmentCreate, session: DBSession, _user: CurrentUser
) -> AppointmentRead:
    lead = await session.get(Lead, payload.lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lead_not_found")

    obj = Appointment(**payload.model_dump())
    session.add(obj)

    # Lead-status side-effect: advance to APPOINTED unless already in a
    # terminal customer state. Same transaction as appointment write.
    if lead.status not in _LEAD_STATUSES_NOT_REGRESSED_TO_APPOINTED:
        lead.status = LeadStatus.APPOINTED

    await session.flush()
    await session.refresh(obj)
    return _to_read(obj)


@router.patch("/{appointment_id}", response_model=AppointmentRead)
async def update_appointment(
    appointment_id: int,
    payload: AppointmentUpdate,
    session: DBSession,
    _user: CurrentUser,
) -> AppointmentRead:
    obj = await session.get(Appointment, appointment_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="appointment_not_found")
    current = AppointmentStatus(obj.status)
    if current in {AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"appointment_terminal:{current.value}",
        )
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await session.refresh(obj)
    return _to_read(obj)


@router.patch("/{appointment_id}/status", response_model=AppointmentRead)
async def transition_appointment_status(
    appointment_id: int,
    payload: AppointmentStatusAction,
    session: DBSession,
    _user: CurrentUser,
) -> AppointmentRead:
    obj = await session.get(Appointment, appointment_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="appointment_not_found")
    next_status = _next_status(obj.status, payload.action)
    obj.status = next_status

    # Lead-status side-effects: complete → visited; cancel does NOT touch lead.
    if next_status is AppointmentStatus.COMPLETED:
        lead = obj.lead
        if lead is not None:
            lead.status = LeadStatus.VISITED

    await session.flush()
    await session.refresh(obj)
    return _to_read(obj)


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_appointment(
    appointment_id: int, session: DBSession, _user: CurrentUser
) -> None:
    obj = await session.get(Appointment, appointment_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="appointment_not_found")
    current = AppointmentStatus(obj.status)
    if current not in {AppointmentStatus.PENDING, AppointmentStatus.CANCELLED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"delete_blocked:{current.value}",
        )
    await session.delete(obj)
