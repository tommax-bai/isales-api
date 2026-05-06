"""Async SQLAlchemy session factory + FastAPI dependency."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def get_engine(url: str | None = None) -> AsyncEngine:
    resolved = url or os.environ["ISALES_DATABASE_URL"]
    return create_async_engine(resolved, future=True)


def get_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def _sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sm = getattr(request.app.state, "sessionmaker", None)
    if sm is None:
        raise RuntimeError("app.state.sessionmaker not configured")
    return sm  # type: ignore[no-any-return]


async def get_session(
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sessionmaker)],
) -> AsyncIterator[AsyncSession]:
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DBSession = Annotated[AsyncSession, Depends(get_session)]
