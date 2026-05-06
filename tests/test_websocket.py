"""WebSocket /ws/calls/{campaign_id} tests.

Auth path uses TestClient (sync; close-code is observable through
WebSocketDisconnect). The end-to-end fan-out path drives ConnectionManager
+ redis_subscriber directly with a fake WebSocket — TestClient's portal runs
the FastAPI app on its own event loop, so a manager set up on the test's
event loop can't actually deliver send_text() across loops. The unit test
covers exactly the path that matters: Redis publish → subscriber
listen → manager.fan_out → ws.send_text.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from isales_common.utils.redis import get_redis as common_redis

from isales_api.auth.jwt import sign_jwt
from isales_api.main import create_app
from isales_api.ws.manager import ConnectionManager
from isales_api.ws.redis_subscriber import channel_for, make_subscriber_factory


@pytest_asyncio.fixture(loop_scope="session")
async def redis_for_ws() -> AsyncIterator[object]:
    url = os.environ.get("ISALES_REDIS_URL", "redis://localhost:6379/0")
    try:
        client = common_redis(url)
        await client.ping()
    except Exception as exc:
        pytest.skip(f"Redis not reachable: {exc}")
    yield client
    await client.aclose()  # type: ignore[attr-defined]


@pytest.fixture
def jwt_app(monkeypatch: pytest.MonkeyPatch) -> object:
    """A minimal app for auth tests — no Redis, no manager required."""
    monkeypatch.setenv("ISALES_JWT_SECRET", "test-secret")
    app = create_app()

    # The auth path closes before manager.connect, so app.state.ws_manager
    # being absent is fine; only an internal_error path would hit it.
    return app


class _FakeWS:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def send_text(self, msg: str) -> None:
        await self.queue.put(msg)


class TestWebSocketAuth:
    def test_missing_token_closes_4401(self, jwt_app: object) -> None:
        from starlette.websockets import WebSocketDisconnect

        with (
            TestClient(jwt_app) as client,
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws/calls/1") as ws,
        ):
            ws.receive_text()
        assert exc_info.value.code == 4401

    def test_invalid_token_closes_4401(self, jwt_app: object) -> None:
        from starlette.websockets import WebSocketDisconnect

        with (
            TestClient(jwt_app) as client,
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws/calls/1?token=not-a-jwt") as ws,
        ):
            ws.receive_text()
        assert exc_info.value.code == 4401

    def test_valid_token_accepts(self, jwt_app: object) -> None:
        # No manager configured on app.state → 1011; but the JWT is valid so
        # the close code MUST NOT be 4401.
        from starlette.websockets import WebSocketDisconnect

        token = sign_jwt({"sub": "admin"})
        with (
            TestClient(jwt_app) as client,
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(f"/ws/calls/1?token={token}") as ws,
        ):
            ws.receive_text()
        assert exc_info.value.code != 4401


@pytest.mark.asyncio(loop_scope="session")
class TestSubscriberFanOut:
    async def test_publish_reaches_connected_clients(
        self, redis_for_ws: object
    ) -> None:
        manager = ConnectionManager(make_subscriber_factory(redis_for_ws))  # type: ignore[arg-type]
        ws = _FakeWS()
        await manager.connect(7, ws)  # type: ignore[arg-type]
        # Allow the subscriber task time to actually subscribe.
        await asyncio.sleep(0.1)
        await redis_for_ws.publish(channel_for(7), '{"hello":"world"}')  # type: ignore[union-attr]
        msg = await asyncio.wait_for(ws.queue.get(), timeout=3)
        assert msg == '{"hello":"world"}'
        await manager.disconnect(7, ws)  # type: ignore[arg-type]
        await manager.shutdown()

    async def test_multiple_clients_share_one_subscriber(
        self, redis_for_ws: object
    ) -> None:
        manager = ConnectionManager(make_subscriber_factory(redis_for_ws))  # type: ignore[arg-type]
        a = _FakeWS()
        b = _FakeWS()
        await manager.connect(8, a)  # type: ignore[arg-type]
        await manager.connect(8, b)  # type: ignore[arg-type]
        await asyncio.sleep(0.1)
        # Manager keeps a single subscriber task per campaign.
        assert len(manager._tasks) == 1  # type: ignore[attr-defined]
        await redis_for_ws.publish(channel_for(8), "shared")  # type: ignore[union-attr]
        m1 = await asyncio.wait_for(a.queue.get(), timeout=3)
        m2 = await asyncio.wait_for(b.queue.get(), timeout=3)
        assert m1 == "shared"
        assert m2 == "shared"
        await manager.disconnect(8, a)  # type: ignore[arg-type]
        await manager.disconnect(8, b)  # type: ignore[arg-type]
        await manager.shutdown()
