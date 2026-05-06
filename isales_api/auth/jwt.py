"""JWT signing — only isales-api signs (per architecture spec).

isales-common ships ``verify_jwt`` for downstream services; signing is
deliberately *not* exported from common, so this module owns it. HS256, 24h
TTL by default. The secret is read from ``ISALES_JWT_SECRET``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt as jose_jwt

ALGORITHM = "HS256"
DEFAULT_TTL = timedelta(hours=24)


def _secret() -> str:
    secret = os.environ.get("ISALES_JWT_SECRET")
    if not secret:
        raise RuntimeError("ISALES_JWT_SECRET is not set")
    return secret


def sign_jwt(claims: dict[str, Any], *, ttl: timedelta = DEFAULT_TTL) -> str:
    """Sign ``claims`` with HS256; ``exp`` is set automatically to now + ttl."""
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        **claims,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    token: str = jose_jwt.encode(payload, _secret(), algorithm=ALGORITHM)
    return token
