"""/leads CRUD + /leads/import streaming CSV bulk."""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from isales_common.enums import LeadStatus
from isales_common.models import Lead
from isales_common.schemas.lead import LeadCreate, LeadRead, LeadUpdate
from sqlalchemy import func, select

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession
from isales_api.schemas import LeadsImportError, LeadsImportResult, Page

router = APIRouter(prefix="/leads", tags=["leads"])

REQUIRED_CSV_COLUMNS = {"campaign_id", "phone"}
BATCH_SIZE = 1000


@router.get("", response_model=Page[LeadRead])
async def list_leads(
    session: DBSession,
    _user: CurrentUser,
    campaign_id: Annotated[int | None, Query()] = None,
    status_filter: Annotated[LeadStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[LeadRead]:
    stmt = select(Lead).order_by(Lead.id)
    count_stmt = select(func.count()).select_from(Lead)
    if campaign_id is not None:
        stmt = stmt.where(Lead.campaign_id == campaign_id)
        count_stmt = count_stmt.where(Lead.campaign_id == campaign_id)
    if status_filter is not None:
        stmt = stmt.where(Lead.status == status_filter)
        count_stmt = count_stmt.where(Lead.status == status_filter)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt)).scalars().all()
    return Page[LeadRead](
        items=[LeadRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{lead_id}", response_model=LeadRead)
async def get_lead(lead_id: int, session: DBSession, _user: CurrentUser) -> LeadRead:
    obj = await session.get(Lead, lead_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lead_not_found")
    return LeadRead.model_validate(obj)


@router.post("", response_model=LeadRead, status_code=status.HTTP_201_CREATED)
async def create_lead(
    payload: LeadCreate, session: DBSession, _user: CurrentUser
) -> LeadRead:
    obj = Lead(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return LeadRead.model_validate(obj)


@router.patch("/{lead_id}", response_model=LeadRead)
async def update_lead(
    lead_id: int, payload: LeadUpdate, session: DBSession, _user: CurrentUser
) -> LeadRead:
    obj = await session.get(Lead, lead_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lead_not_found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await session.refresh(obj)
    return LeadRead.model_validate(obj)


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(lead_id: int, session: DBSession, _user: CurrentUser) -> None:
    obj = await session.get(Lead, lead_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lead_not_found")
    await session.delete(obj)


@router.post("/import", response_model=LeadsImportResult)
async def import_leads(
    session: DBSession,
    _user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
) -> LeadsImportResult:
    """Stream-parse a CSV; commit every BATCH_SIZE rows; report per-row errors.

    Required columns: ``campaign_id``, ``phone``.
    Optional: ``name``, ``source``, ``custom_data`` (JSON-encoded string).
    """
    raw = await file.read()
    text = raw.decode("utf-8-sig")  # tolerate BOM
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not REQUIRED_CSV_COLUMNS.issubset(reader.fieldnames):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"csv_missing_columns: required {sorted(REQUIRED_CSV_COLUMNS)}",
        )

    success = 0
    errors: list[LeadsImportError] = []
    batch: list[Lead] = []

    async def _flush() -> None:
        nonlocal batch, success
        if not batch:
            return
        session.add_all(batch)
        await session.flush()
        success += len(batch)
        batch = []

    for i, row in enumerate(reader, start=2):  # row 2 = first data row
        try:
            campaign_id = int(row["campaign_id"])
            phone = (row["phone"] or "").strip()
            if not phone:
                raise ValueError("phone is empty")
            custom = row.get("custom_data") or ""
            custom_dict: dict[str, object] = json.loads(custom) if custom.strip() else {}
            batch.append(
                Lead(
                    campaign_id=campaign_id,
                    phone=phone,
                    name=row.get("name") or None,
                    source=row.get("source") or None,
                    custom_data=custom_dict,
                )
            )
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append(LeadsImportError(row=i, message=str(exc)))
            continue

        if len(batch) >= BATCH_SIZE:
            await _flush()

    await _flush()
    return LeadsImportResult(
        success_count=success, error_count=len(errors), errors=errors
    )
