"""/ws/calls/{campaign_id} — WebSocket fan-out for engine events."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from isales_common.utils.jwt import InvalidJWT, verify_jwt

from isales_api.ws.manager import ConnectionManager

logger = logging.getLogger(__name__)
router = APIRouter()

# Custom close code per design.md decision 2 — "4401" lets the frontend
# distinguish auth failure from generic 1008 policy violation.
WS_CLOSE_AUTH_FAILED = 4401


def _verify_token_or_none(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    secret = os.environ.get("ISALES_JWT_SECRET")
    if not secret:
        return None
    try:
        return verify_jwt(token, secret)
    except InvalidJWT:
        return None


@router.websocket("/ws/calls/{campaign_id}")
async def ws_calls(
    ws: WebSocket,
    campaign_id: int,
    token: str | None = Query(default=None),
) -> None:
    claims = _verify_token_or_none(token)
    if claims is None:
        # MUST close before accept per design — but Starlette requires accept()
        # before close() in some servers. The portable path is accept then close
        # with a custom 4401 code; the client sees the disconnect with that code.
        await ws.accept()
        await ws.close(code=WS_CLOSE_AUTH_FAILED)
        return

    manager: ConnectionManager | None = getattr(ws.app.state, "ws_manager", None)
    if manager is None:
        await ws.accept()
        await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await ws.accept()
    await manager.connect(campaign_id, ws)
    try:
        while True:
            # We don't expect inbound messages from the client; receive_text
            # blocks until disconnect.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(campaign_id, ws)
