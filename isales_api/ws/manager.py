"""Connection manager + subscriber lifecycle for /ws/calls.

Single-instance v1: per-campaign dict of WebSocket connections + a single
shared Redis Pub/Sub subscriber task. The subscriber starts on first connect
and stops shortly after the last disconnect (delayed shutdown to absorb
reconnect churn).

Multi-instance support is explicitly out of scope; an OpenSpec change would
introduce sticky sessions or Redis Streams + cross-instance fan-out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

logger = logging.getLogger(__name__)


SubscriberFactory = Callable[[int, "ConnectionManager"], Awaitable[asyncio.Task[None]]]
"""Builds and starts a background task subscribing to the campaign's Redis
channel and forwarding events into ``manager.fan_out``. Returns the task so
the manager can cancel it."""


class ConnectionManager:
    SHUTDOWN_DELAY_SECONDS = 5.0

    def __init__(self, subscriber_factory: SubscriberFactory) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._shutdown_handles: dict[int, asyncio.TimerHandle] = {}
        self._subscriber_factory = subscriber_factory
        self._lock = asyncio.Lock()

    async def connect(self, campaign_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._cancel_pending_shutdown(campaign_id)
            connections = self._connections.setdefault(campaign_id, set())
            connections.add(ws)
            if campaign_id not in self._tasks:
                self._tasks[campaign_id] = await self._subscriber_factory(campaign_id, self)
                logger.info("ws.manager: started subscriber for campaign %s", campaign_id)

    async def disconnect(self, campaign_id: int, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(campaign_id)
            if conns is None:
                return
            conns.discard(ws)
            if conns:
                return
            self._connections.pop(campaign_id, None)
            self._schedule_shutdown(campaign_id)

    async def fan_out(self, campaign_id: int, message: str) -> None:
        targets = list(self._connections.get(campaign_id, ()))
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                logger.exception(
                    "ws.manager: send failed for campaign %s; dropping connection",
                    campaign_id,
                )
                # The active route handler will detect the closed socket and
                # call disconnect; here we just stop trying to write to it.

    def _schedule_shutdown(self, campaign_id: int) -> None:
        loop = asyncio.get_running_loop()
        if campaign_id in self._shutdown_handles:
            return
        self._shutdown_handles[campaign_id] = loop.call_later(
            self.SHUTDOWN_DELAY_SECONDS,
            lambda: asyncio.create_task(self._maybe_stop_subscriber(campaign_id)),
        )

    def _cancel_pending_shutdown(self, campaign_id: int) -> None:
        handle = self._shutdown_handles.pop(campaign_id, None)
        if handle is not None:
            handle.cancel()

    async def _maybe_stop_subscriber(self, campaign_id: int) -> None:
        async with self._lock:
            self._shutdown_handles.pop(campaign_id, None)
            # Re-check: a fresh connect may have arrived between scheduling and now.
            if campaign_id in self._connections and self._connections[campaign_id]:
                return
            task = self._tasks.pop(campaign_id, None)
            if task is None:
                return
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        logger.info("ws.manager: stopped subscriber for campaign %s", campaign_id)

    async def shutdown(self) -> None:
        """Cancel every subscriber task. Called from app lifespan exit."""
        for handle in self._shutdown_handles.values():
            handle.cancel()
        self._shutdown_handles.clear()
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(BaseException):
                await t
