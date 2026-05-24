"""isales-api FastAPI entrypoint.

PR #1 (skeleton) wires the bare app + ``/health``; subsequent PRs add auth,
CRUD routers, Campaign start/pause queue, and the WebSocket fan-out.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from isales_api.auth.router import router as auth_router
from isales_api.common.db import get_engine, get_sessionmaker
from isales_api.common.redis import get_redis
from isales_api.routers import (
    analytics,
    appointments,
    calls,
    campaigns,
    edge_devices,
    filler_sets,
    handoff_tasks,
    holidays,
    leads,
    prompt_versions,
    provider_credentials,
    role_configs,
    voice_models,
    ws,
)
from isales_api.ws.manager import ConnectionManager
from isales_api.ws.redis_subscriber import make_subscriber_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = get_engine()
    sessionmaker = get_sessionmaker(engine)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    redis = get_redis()
    app.state.redis = redis
    app.state.ws_manager = ConnectionManager(make_subscriber_factory(redis))
    try:
        yield
    finally:
        ws_manager = getattr(app.state, "ws_manager", None)
        if ws_manager is not None:
            await ws_manager.shutdown()
        await redis.close()
        await engine.dispose()


def create_app() -> FastAPI:
    use_lifespan = lifespan if os.environ.get("ISALES_DATABASE_URL") else None
    app = FastAPI(title="isales-api", version="0.1.0", lifespan=use_lifespan)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(campaigns.router)
    app.include_router(leads.router)
    app.include_router(voice_models.router)
    app.include_router(holidays.router)
    app.include_router(handoff_tasks.router)
    app.include_router(calls.router)
    app.include_router(appointments.router)
    app.include_router(role_configs.router)
    app.include_router(prompt_versions.router)
    app.include_router(filler_sets.router)
    app.include_router(provider_credentials.router)
    app.include_router(analytics.router)
    app.include_router(edge_devices.router)
    app.include_router(ws.router)
    return app


app = create_app()


def run() -> None:
    """Console-script entry point: ``isales-api``."""
    uvicorn.run(
        "isales_api.main:app",
        host="0.0.0.0",
        port=8000,
        ws="auto",
        workers=1,
    )
