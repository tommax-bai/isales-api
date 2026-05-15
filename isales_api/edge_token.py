"""Cloud-edge bearer-token mint (A2).

Spec: arch-cloud-edge-split § service-communication Requirement "云-边控制面"
      Scenario "gRPC 鉴权" — token signed by isales-api with same secret as the
      frontend JWT but a distinct ``aud`` so the engine's TokenVerifier can
      reject a stolen frontend JWT.

A2 uses a static long-lived token per edge box (written to ``edge.env``).
C2 ``multi-tenant-roles-and-leads`` will replace this with an activation-
code → tenant-scoped flow; the JWT shape stays compatible.

Claims:
    sub  = edge_device_id                  (cloud-side PK; engine binds the
                                             stream to this identity)
    aud  = ``cloud-edge``                  (audience isolation from frontend
                                             tokens whose aud is the API)
    scope = ``edge``                       (so the verifier can fail fast on
                                             a frontend token)
    iat / exp                              (HS256 standard claims)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt as jose_jwt

ALGORITHM = "HS256"
AUDIENCE = "cloud-edge"
SCOPE = "edge"

# A2 default: one year. Operators may shorten with --ttl. C2 cuts this to
# tenant-scoped weekly rotation.
DEFAULT_TTL = timedelta(days=365)


def _secret() -> str:
    secret = os.environ.get("ISALES_JWT_SECRET")
    if not secret:
        raise RuntimeError("ISALES_JWT_SECRET is not set")
    return secret


def mint_edge_token(
    edge_device_id: str,
    *,
    ttl: timedelta = DEFAULT_TTL,
    secret: str | None = None,
    now: datetime | None = None,
) -> str:
    """Sign an edge-device JWT. Returns the encoded compact JWT string.

    ``edge_device_id`` becomes the ``sub`` claim; the engine's
    ``TokenVerifier.verify`` extracts it and binds it to the gRPC stream
    (one bidi stream per edge identity).
    """

    if not edge_device_id:
        raise ValueError("edge_device_id is required")

    now = now or datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": edge_device_id,
        "aud": AUDIENCE,
        "scope": SCOPE,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    token: str = jose_jwt.encode(claims, secret or _secret(), algorithm=ALGORITHM)
    return token


def _parse_ttl(text: str) -> timedelta:
    """Parse a ttl string like ``365d``, ``24h``, ``30m``, or seconds.

    Used by the CLI; intentionally narrow so a typo errors immediately instead
    of silently shortening a token.
    """

    text = text.strip().lower()
    if not text:
        raise ValueError("ttl is required")
    unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text[-1] in unit_seconds:
        try:
            value = int(text[:-1])
        except ValueError as exc:
            raise ValueError(f"bad ttl value: {text!r}") from exc
        return timedelta(seconds=value * unit_seconds[text[-1]])
    try:
        return timedelta(seconds=int(text))
    except ValueError as exc:
        raise ValueError(
            f"bad ttl {text!r}: use e.g. 365d / 24h / 30m / 86400"
        ) from exc
