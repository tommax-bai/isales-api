"""isales-api-local DTOs not present in isales-common.

Mostly nested-write variants of children resources (drop ``campaign_id`` —
server fills it from the path) and pagination wrappers. Reusing the common
``*Read`` types directly where practical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from isales_common.enums import (
    GenerationStatus,
    RoleKind,
)
from isales_common.schemas._base import AppModel, ORMModel
from isales_common.schemas.callback import CallbackConfigRead
from isales_common.schemas.campaign import CampaignBase, CampaignRead
from isales_common.schemas.jsonb import CallbackTrigger, RetryPolicy
from isales_common.schemas.role_config import RoleConfigRead
from pydantic import Field

T = TypeVar("T")


class Page(AppModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ── nested children (no campaign_id; server fills it) ────────────────────────


class RoleConfigNestedWrite(AppModel):
    kind: RoleKind
    model: str = Field(min_length=1, max_length=128)
    current_prompt_version_id: int | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    ext_params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class FillerPhraseNestedWrite(AppModel):
    phrase: str = Field(min_length=1, max_length=512)
    audio_url: str | None = None
    generation_status: GenerationStatus = GenerationStatus.PENDING


class FillerSetNestedWrite(AppModel):
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0
    phrases: list[FillerPhraseNestedWrite] = Field(default_factory=list)


class FillerPhraseRead(ORMModel):
    id: int
    filler_set_id: int
    phrase: str
    audio_url: str | None = None
    generation_status: GenerationStatus
    created_at: datetime
    updated_at: datetime


class FillerSetRead(ORMModel):
    id: int
    campaign_id: int
    name: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


class CallbackConfigNestedWrite(AppModel):
    name: str = Field(min_length=1, max_length=128)
    trigger: CallbackTrigger
    url: str = Field(min_length=1, max_length=1024)
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    payload_template: str = Field(min_length=1)
    retry_policy: RetryPolicy
    timeout_seconds: int | None = Field(default=None, ge=1)
    enabled: bool = True


# ── campaign aggregate request/response ──────────────────────────────────────


class CampaignNestedCreate(CampaignBase):
    role_configs: list[RoleConfigNestedWrite] = Field(default_factory=list)
    filler_sets: list[FillerSetNestedWrite] = Field(default_factory=list)
    callback_configs: list[CallbackConfigNestedWrite] = Field(default_factory=list)


class CampaignNestedUpdate(AppModel):
    """Replace-style PATCH: any field set replaces; children lists fully
    overwrite when present (server deletes old children, inserts new)."""

    # Mirror CampaignUpdate (all optional). For brevity, only the most-used
    # fields surface here; the detail view shows the rest.
    name: str | None = Field(default=None, min_length=1, max_length=255)
    voice_id: int | None = None
    concurrency: int | None = Field(default=None, ge=1)

    role_configs: list[RoleConfigNestedWrite] | None = None
    filler_sets: list[FillerSetNestedWrite] | None = None
    callback_configs: list[CallbackConfigNestedWrite] | None = None


class FillerSetWithPhrasesRead(FillerSetRead):
    phrases: list[FillerPhraseRead] = Field(default_factory=list)


class CampaignDetailRead(CampaignRead):
    role_configs: list[RoleConfigRead] = Field(default_factory=list)
    filler_sets: list[FillerSetWithPhrasesRead] = Field(default_factory=list)
    callback_configs: list[CallbackConfigRead] = Field(default_factory=list)


# ── campaign-device association ──────────────────────────────────────────────


class CampaignDeviceAttach(AppModel):
    device_id: int = Field(gt=0)


class CampaignDeviceRead(ORMModel):
    id: int
    campaign_id: int
    device_id: int
    created_at: datetime
    updated_at: datetime


# ── holidays (no schema in isales-common; tiny local DTOs) ───────────────────


class HolidayCreate(AppModel):
    date: str  # ISO date YYYY-MM-DD
    name: str = Field(min_length=1, max_length=128)
    region: str = Field(default="CN", min_length=1, max_length=32)


class HolidayUpdate(AppModel):
    date: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, min_length=1, max_length=32)


class HolidayRead(ORMModel):
    id: int
    date: str  # serialised from date object via Pydantic
    name: str
    region: str
    created_at: datetime
    updated_at: datetime


# ── leads/import response ────────────────────────────────────────────────────


class LeadsImportError(AppModel):
    row: int
    message: str


class LeadsImportResult(AppModel):
    success_count: int
    error_count: int
    errors: list[LeadsImportError] = Field(default_factory=list)
