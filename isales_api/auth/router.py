"""/auth/login + /auth/me routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from isales_api.auth.deps import CurrentUser
from isales_api.auth.jwt import sign_jwt
from isales_api.auth.users import authenticate

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    if not authenticate(form.username, form.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=sign_jwt({"sub": form.username, "role": "admin"}))


@router.get("/me", response_model=dict[str, Any])
async def me(user: CurrentUser) -> dict[str, Any]:
    return user
