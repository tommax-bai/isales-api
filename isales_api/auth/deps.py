"""FastAPI auth dependencies — verify JWT and inject ``current_user``."""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from isales_common.utils.jwt import InvalidJWT, verify_jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=True)


def _secret() -> str:
    secret = os.environ.get("ISALES_JWT_SECRET")
    if not secret:
        raise RuntimeError("ISALES_JWT_SECRET is not set")
    return secret


def current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> dict[str, Any]:
    try:
        return verify_jwt(token, _secret())
    except InvalidJWT as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]
