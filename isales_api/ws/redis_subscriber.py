"""Redis Pub/Sub → WebSocket fan-out task factory."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from redis.asyncio import Redis

from isales_api.ws.manager import ConnectionManager

logger = logging.getLogger(__name__)


def channel_for(campaign_id: int) -> str:
    return f"engine:events:campaign:{campaign_id}"


def make_subscriber_factory(redis: Redis[Any]) -> Any:
    """Build a SubscriberFactory closure that uses ``redis`` for pub/sub.

    Returned callable matches ``ConnectionManager.SubscriberFactory``: takes
    (campaign_id, manager), returns an asyncio.Task subscribing to
    ``engine:events:campaign:{id}`` and forwarding each raw message to
    ``manager.fan_out`` verbatim (no JSON re-encoding — see
    service-communication spec § "消息形状由 message-contract spec 约束").
    """

    async def _factory(
        campaign_id: int, manager: ConnectionManager
    ) -> asyncio.Task[None]:
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_for(campaign_id))

        async def _run() -> None:
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    await manager.fan_out(campaign_id, raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "ws.subscriber: error on campaign %s; closing pubsub",
                    campaign_id,
                )
            finally:
                try:
                    await pubsub.unsubscribe(channel_for(campaign_id))
                    await pubsub.close()
                except Exception:
                    pass

        return asyncio.create_task(_run())

    return _factory
