"""Shared fixtures for isales-api integration tests.

Same pattern as isales-telephony: real PG via ``ISALES_TEST_DATABASE_URL``,
NullPool engine to avoid asyncpg cross-loop pain. Auth dep is overridden
when needed by per-test fixtures (per-router test files set up their own
override; the base ``client`` fixture installs a permissive bypass).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import bcrypt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from isales_common.models import Base
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from isales_api.auth.deps import current_user
from isales_api.main import create_app

DEFAULT_TEST_URL = "postgresql+asyncpg://bears@localhost:5432/isales_api_test"


def _resolve_url() -> str:
    return (
        os.environ.get("ISALES_TEST_DATABASE_URL")
        or os.environ.get("ISALES_DATABASE_URL")
        or DEFAULT_TEST_URL
    )


@pytest.fixture(scope="session", autouse=True)
def _admin_env(request: pytest.FixtureRequest) -> None:
    """Set admin creds + JWT secret env vars for the whole session."""
    pw_hash = bcrypt.hashpw(b"changeme", bcrypt.gensalt()).decode()
    os.environ.setdefault("ISALES_ADMIN_USER", "admin")
    os.environ.setdefault("ISALES_ADMIN_PASSWORD_HASH", pw_hash)
    os.environ.setdefault("ISALES_JWT_SECRET", "test-secret")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(_resolve_url(), future=True, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        pytest.skip(f"PG not reachable for integration tests: {exc}")
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def clean_engine(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE campaign, role_config, filler_set, filler_phrase, "
            "callback_config, campaign_device, device, sim_card, "
            "device_sim_binding, appointment, lead, voice_model, holiday "
            "RESTART IDENTITY CASCADE"
        )
    yield engine


@pytest_asyncio.fixture(loop_scope="session")
async def client(clean_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.engine = clean_engine
    app.state.sessionmaker = async_sessionmaker(clean_engine, expire_on_commit=False)
    app.dependency_overrides[current_user] = lambda: {"sub": "test-admin", "role": "admin"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
