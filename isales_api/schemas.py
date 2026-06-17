"""isales-api-local DTOs not present in isales-common.

Mostly nested-write variants of children resources (drop ``campaign_id`` —
server fills it from the path) and pagination wrappers. Reusing the common
``*Read`` types directly where practical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from isales_common.enums import (
    ContinuousInterruptionStrategy,
    RoleKind,
)
from isales_common.schemas._base import AppModel, ORMModel
from isales_common.schemas.callback import CallbackConfigRead
from isales_common.schemas.campaign import CampaignBase, CampaignRead
from isales_common.schemas.jsonb import (
    CallbackTrigger,
    ExtractionField,
    InterruptionRule,
    RetryPolicy,
    RoutingRule,
    TimeWindow,
    ToolConfig,
)
from isales_common.schemas.role_config import RoleConfigRead
from pydantic import Field

T = TypeVar("T")


class Page(AppModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class TtsPreviewRequest(AppModel):
    """Greeting 试听 request (campaign-greeting-tts-preview). Stateless — uses
    the form's current (possibly unsaved) text + voice, no campaign_id. The
    200-char cap bounds vendor cost (a greeting is ~25 chars)."""

    text: str = Field(min_length=1, max_length=200)
    voice_id: str = Field(min_length=1, max_length=128)


# ── nested children (no campaign_id; server fills it) ────────────────────────


class RoleConfigNestedWrite(AppModel):
    kind: RoleKind
    # Routing label for referee/restructure rows (referenced by routing_rules).
    label: str | None = Field(default=None, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    current_prompt_version_id: int | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    ext_params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


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
    callback_configs: list[CallbackConfigNestedWrite] = Field(default_factory=list)


class CampaignNestedUpdate(AppModel):
    """Replace-style PATCH: any field set replaces; children lists fully
    overwrite when present (server deletes old children, inserts new).

    All CampaignBase fields surface here as optional so the 9-tab edit form
    in isales-web can persist any subset (impl-web-polish PR #4-#7).
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    voice_id: str | None = Field(default=None, max_length=128)
    default_replies: list[str] | None = None
    concurrency: int | None = Field(default=None, ge=1)
    time_windows: list[TimeWindow] | None = None
    extraction_fields: list[ExtractionField] | None = None

    # multi-referee routing (engine-multi-referee-and-restructure).
    routing_rules: list[RoutingRule] | None = None
    max_continuous_restructure: int | None = Field(default=None, ge=0)
    # engine-auto-restructure-on-interrupt: declared here or the bulk
    # model_dump(exclude_unset) PATCH apply would silently drop it.
    auto_restructure_on_interrupt: bool | None = None

    # gating + multi-persona (engine-tools-multidialogue-gating). update_campaign
    # reads payload.tools unconditionally — these MUST be declared here or every
    # PATCH 500s (AttributeError). Bounds mirror CampaignBase.
    tools: dict[str, ToolConfig] | None = None
    persona_fanout_cap: int | None = Field(default=None, ge=1, le=3)
    referee_timeout_ms: int | None = Field(default=None, gt=0)
    referee_fail_open_route: str | None = Field(
        default=None, min_length=1, max_length=64
    )

    max_silence_activations: int | None = None
    silence_threshold_ms: int | None = None
    silence_phrases: list[str] | None = None
    silence_hangup_phrase: str | None = None

    # ASR EOS endpoint threshold in ms (pipeline-latency-tail § A). NULL →
    # engine default 400ms.
    asr_eos_silence_ms: int | None = None

    # ambient background mix (engine-ambient-background-mix). Declared here or
    # the model_dump(exclude_unset) PATCH apply would silently drop them.
    ambient_audio: str | None = Field(default=None, max_length=128)
    ambient_gain: float | None = Field(default=None, ge=0.0, le=1.0)

    # filler time-gate in ms (tts-cache-and-gated-filler § B). NULL → 600ms.
    filler_delay_ms: int | None = None

    wrap_up_max_rounds: int | None = None
    wrap_up_max_seconds: int | None = None
    wrap_up_closing_phrases: list[str] | None = None
    # engine-wrap-up-silence-hangup: declared here or the bulk
    # model_dump(exclude_unset) PATCH apply would reject it (extra=forbid).
    # Bounds mirror CampaignBase (ge=0).
    wrap_up_silence_hangup_ms: int | None = Field(default=None, ge=0)

    greeting: str | None = None
    filler_enabled: bool | None = None

    interruption_rules: InterruptionRule | None = None
    interruption_whitelist: list[str] | None = None
    interruption_min_duration_ms: int | None = None
    interruption_min_chars: int | None = Field(default=None, ge=1)
    max_continuous_interruptions: int | None = None
    continuous_interruption_strategy: ContinuousInterruptionStrategy | None = None

    transfer_keyword_enabled: bool | None = None
    transfer_keywords: list[str] | None = None
    transfer_intent_enabled: bool | None = None
    transfer_intent_threshold: float | None = None
    transfer_round_enabled: bool | None = None
    transfer_round_threshold: int | None = None
    transfer_llm_enabled: bool | None = None
    transfer_llm_prompt_version_id: int | None = None
    transfer_phrases: list[str] | None = None

    retry_intervals: list[int] | None = None
    retry_max_count: int | None = None
    follow_up_interval_days: int | None = None
    follow_up_max_count: int | None = None

    respect_holidays: bool | None = None

    role_configs: list[RoleConfigNestedWrite] | None = None
    filler_phrases: list[str] | None = None
    callback_configs: list[CallbackConfigNestedWrite] | None = None


class RoutingRulesReplace(AppModel):
    """PUT /campaigns/{id}/routing-rules body (engine-multi-referee §5.2)."""

    routing_rules: list[RoutingRule] = Field(default_factory=list)
    max_continuous_restructure: int | None = Field(default=None, ge=0)


class RoutingRulesRead(AppModel):
    routing_rules: list[RoutingRule] = Field(default_factory=list)
    max_continuous_restructure: int = 2


class CampaignDetailRead(CampaignRead):
    role_configs: list[RoleConfigRead] = Field(default_factory=list)
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


# ── campaign progress ────────────────────────────────────────────────────────


class CampaignProgress(AppModel):
    """按 lead.status 聚合的 campaign 外呼进度。

    Spec: web-admin-campaign-workflow — campaign 详情页「外呼进度」数据源。

    ``is_active`` 来自 scheduler 维护的 Redis SET（source of truth）；当 api
    进程未持有 redis 连接（如部分单元测试）时降级为 False。
    """

    campaign_id: int
    total: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    is_active: bool = False
