"""/handoff-tasks read-only listing.

v1 stage 2: GET only. Mutating endpoints (assign, pickup, complete) return
501 Not Implemented because worker (stage 3) hasn't shipped — there are no
``handoff_task`` rows to mutate, and the agent UI flow only exists in stage 7.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from isales_common.enums import HandoffStatus
from isales_common.models import HandoffTask
from isales_common.schemas.handoff import HandoffTaskRead
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import Page

router = APIRouter(prefix="/handoff-tasks", tags=["handoff-tasks"])


@router.get(
    "",
    response_model=Page[HandoffTaskRead],
    description="v1 stage 2: this list is empty until worker (stage 3) ships.",
)
async def list_handoff_tasks(
    session: DBSession,
    _user: CurrentUser,
    status_filter: Annotated[HandoffStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[HandoffTaskRead]:
    stmt = select(HandoffTask).order_by(HandoffTask.id.desc())
    count_stmt = select(func.count()).select_from(HandoffTask)
    if status_filter is not None:
        stmt = stmt.where(HandoffTask.status == status_filter)
        count_stmt = count_stmt.where(HandoffTask.status == status_filter)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[HandoffTaskRead](
        items=[HandoffTaskRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{task_id}", response_model=HandoffTaskRead)
async def get_handoff_task(
    task_id: int, session: DBSession, _user: CurrentUser
) -> HandoffTaskRead:
    obj = await session.get(HandoffTask, task_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="handoff_task_not_found")
    return HandoffTaskRead.model_validate(obj)


@router.post(
    "/{task_id}/pick-up",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    description="Lifecycle endpoint — see stage 3 (worker) for implementation.",
)
async def pick_up_stub(task_id: int, _user: CurrentUser) -> dict[str, str]:
    _ = task_id
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="lifecycle_in_stage_3",
    )


@router.post(
    "/{task_id}/complete",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    description="Lifecycle endpoint — see stage 3 (worker) for implementation.",
)
async def complete_stub(task_id: int, _user: CurrentUser) -> dict[str, str]:
    _ = task_id
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="lifecycle_in_stage_3",
    )
