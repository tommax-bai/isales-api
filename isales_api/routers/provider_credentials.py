"""/provider-credentials — admin-managed encrypted provider credential CRUD.

Spec: provider-credential capability § "Admin CRUD HTTP 端点".

每行一个 ``(provider_id, field_name)`` 字段，cipher_text 由
:func:`isales_common.utils.crypto.encrypt` 加密 (Fernet, urlsafe base64)。
本 router 永不在响应中回明文；写入时接受明文 plaintext_value 加密后落库。

`provider_id` / `field_name` 白名单与 ``isales-engine`` factory
``KNOWN_*_PROVIDERS`` 同步 — 增加新 provider 时改 ``ALLOWED_PROVIDER_IDS``
两端同时改。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from isales_common.credentials import CredentialStore
from isales_common.models import ProviderCredential
from isales_common.schemas.provider_credential import (
    ProviderCredentialRead,
    ProviderCredentialUpsert,
)
from isales_common.utils.crypto import CryptoError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from isales_api.auth.deps import CurrentUser
from isales_api.common.db import DBSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provider-credentials", tags=["provider-credentials"])

# 与 isales-engine `providers/factory.py::KNOWN_*_PROVIDERS` 同步。新增 provider 时两端必须一起改。
ALLOWED_PROVIDER_IDS = frozenset({"volcengine", "openai", "dashscope", "mock"})

ALLOWED_FIELD_NAMES = frozenset(
    {
        "api_key",          # openai / dashscope
        "app_key",          # volcengine (与 app_token 配对)
        "app_token",        # volcengine
        "endpoint",         # 通用 base url
        "asr_endpoint",     # volcengine ASR-specific
        "tts_endpoint",     # volcengine TTS-specific
        "default_model",    # 默认 model name
        "enabled",          # "true" / "false"
    }
)


def _sub(user: dict) -> str | None:
    """JWT subject claim — 写 updated_by 用。"""
    sub = user.get("sub")
    return str(sub) if sub is not None else None


def _to_read(obj: ProviderCredential) -> ProviderCredentialRead:
    """ORM 行 → API DTO；plaintext 解密后立即 mask，不进 API response。"""
    try:
        plaintext = CredentialStore.decrypt(obj.cipher_text)
        masked = CredentialStore.mask(plaintext)
    except CryptoError:
        masked = "********"  # 解密失败 (主密钥变更?) UI 仍可看到 row 存在
    return ProviderCredentialRead(
        id=obj.id,
        provider_id=obj.provider_id,
        field_name=obj.field_name,
        masked_value=masked,
        updated_by=obj.updated_by,
        updated_at=obj.updated_at,
    )


@router.get("", response_model=list[ProviderCredentialRead])
async def list_credentials(
    session: DBSession,
    _user: CurrentUser,
) -> list[ProviderCredentialRead]:
    """所有 provider 的所有字段；按 (provider_id, field_name) 排序。"""
    stmt = select(ProviderCredential).order_by(
        ProviderCredential.provider_id, ProviderCredential.field_name
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_read(r) for r in rows]


@router.get("/{provider_id}", response_model=list[ProviderCredentialRead])
async def list_by_provider(
    provider_id: str,
    session: DBSession,
    _user: CurrentUser,
) -> list[ProviderCredentialRead]:
    """单个 provider 的全部字段。"""
    if provider_id not in ALLOWED_PROVIDER_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown_provider_id:{provider_id} (allowed: {sorted(ALLOWED_PROVIDER_IDS)})",
        )
    stmt = (
        select(ProviderCredential)
        .where(ProviderCredential.provider_id == provider_id)
        .order_by(ProviderCredential.field_name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_read(r) for r in rows]


@router.post("", response_model=ProviderCredentialRead, status_code=status.HTTP_201_CREATED)
async def upsert_credential(
    body: ProviderCredentialUpsert,
    session: DBSession,
    user: CurrentUser,
) -> ProviderCredentialRead:
    """Upsert by (provider_id, field_name)。加密后写入；响应只含 masked。"""
    if body.provider_id not in ALLOWED_PROVIDER_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown_provider_id:{body.provider_id} (allowed: {sorted(ALLOWED_PROVIDER_IDS)})",
        )
    if body.field_name not in ALLOWED_FIELD_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown_field_name:{body.field_name} (allowed: {sorted(ALLOWED_FIELD_NAMES)})",
        )

    cipher = CredentialStore.encrypt(body.plaintext_value)
    sub = _sub(user)
    stmt = pg_insert(ProviderCredential).values(
        provider_id=body.provider_id,
        field_name=body.field_name,
        cipher_text=cipher,
        updated_by=sub,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["provider_id", "field_name"],
        set_={"cipher_text": stmt.excluded.cipher_text, "updated_by": sub},
    ).returning(ProviderCredential)
    row = (await session.execute(stmt)).scalar_one()
    await session.commit()
    await session.refresh(row)
    return _to_read(row)


@router.delete("/{cred_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    cred_id: int,
    session: DBSession,
    _user: CurrentUser,
) -> None:
    obj = await session.get(ProviderCredential, cred_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="credential_not_found")
    await session.delete(obj)
    await session.commit()


@router.post("/reload-hint", status_code=status.HTTP_202_ACCEPTED)
async def reload_hint(_user: CurrentUser) -> dict[str, str]:
    """提示运维重启 engine 让凭据变更生效。v1.0 不实装 live reload。"""
    logger.info("cred_reload_required user=%s", _user.get("sub"))
    return {
        "message": "credentials updated; restart isales-engine to apply",
        "command": "systemctl restart isales-engine",
    }
