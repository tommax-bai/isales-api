"""Dev-only CLI: stream fake EngineEvent messages into Redis Pub/Sub.

Usage::

    python -m scripts.fake_engine_events --campaign-id 1 [--rate-hz 1] [--duration-s 60]

Cycles through a small, valid set of EngineEvent subclasses (status_changed /
asr_partial / call_started / call_ended) so the WebSocket end at /ws/calls
sees a realistic-shaped feed during stage-2 development. Stage 4 ships the
real engine and this script is retired.

NOT exposed as a console entry-point — running it in production is a misuse
(see impl-api proposal § "mock 事件 publisher：dev 脚本不进 production").
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import os
from datetime import UTC, datetime

from isales_common.enums import CallStatus, HangupCause
from isales_common.schemas.messages import (
    ASRPartial,
    CallEndedEvent,
    CallStarted,
    StatusChanged,
)
from isales_common.utils.redis import get_redis

logger = logging.getLogger(__name__)


def _channel(campaign_id: int) -> str:
    return f"engine:events:campaign:{campaign_id}"


def _next_event(call_record_id: int, idx: int) -> object:
    """Cycle through a small, deterministic set of valid EngineEvents."""
    sample = [
        CallStarted(call_record_id=call_record_id, started_at=datetime.now(UTC)),
        StatusChanged(call_record_id=call_record_id, status=CallStatus.IN_CALL),
        ASRPartial(
            call_record_id=call_record_id,
            text=f"用户说话片段 #{idx}",
            timestamp_ms=idx * 1000,
        ),
        StatusChanged(call_record_id=call_record_id, status=CallStatus.IN_CALL),
        CallEndedEvent(
            call_record_id=call_record_id,
            hangup_cause=HangupCause.NORMAL_CLEARING,
            ended_at=datetime.now(UTC),
        ),
    ]
    return sample[idx % len(sample)]


async def main() -> int:
    parser = argparse.ArgumentParser(prog="fake_engine_events")
    parser.add_argument("--campaign-id", type=int, required=True)
    parser.add_argument(
        "--rate-hz",
        type=float,
        default=1.0,
        help="messages per second (default 1)",
    )
    parser.add_argument(
        "--duration-s",
        type=int,
        default=60,
        help="how long to publish, in seconds (default 60)",
    )
    parser.add_argument(
        "--call-record-id",
        type=int,
        default=1,
        help="call_record_id stamped on every event (default 1)",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("ISALES_REDIS_URL", "redis://localhost:6379/0"),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    redis = get_redis(args.redis_url)
    channel = _channel(args.campaign_id)
    interval = 1.0 / args.rate_hz if args.rate_hz > 0 else 1.0
    logger.info(
        "publishing %.1f msg/s to %s for %ds (call_record_id=%s)",
        args.rate_hz,
        channel,
        args.duration_s,
        args.call_record_id,
    )

    deadline = asyncio.get_event_loop().time() + args.duration_s
    sent = 0
    try:
        for idx in itertools.count():
            if asyncio.get_event_loop().time() >= deadline:
                break
            evt = _next_event(args.call_record_id, idx)
            payload = evt.model_dump_json()  # type: ignore[attr-defined]
            await redis.publish(channel, payload)
            sent += 1
            if sent % 10 == 0:
                logger.info("sent %d", sent)
            await asyncio.sleep(interval)
    finally:
        logger.info("done; total sent=%d", sent)
        await redis.aclose()  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
