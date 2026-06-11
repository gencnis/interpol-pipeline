from __future__ import annotations

import json
from typing import Any

import redis as redis_sync

from app.common.logging import get_logger

log = get_logger(__name__)


class RedisPublisher:
    def __init__(self, redis_url: str, channel: str) -> None:
        self._client = redis_sync.from_url(redis_url)
        self._channel = channel

    def publish(
        self,
        event: str,
        notice_id: str,
        notice_name: str | None,
        diff: dict[str, Any] | None,
        recorded_at: str,
    ) -> None:
        payload = json.dumps(
            {
                "event": event,
                "notice_id": notice_id,
                "notice_name": notice_name,
                "diff": diff,
                "recorded_at": recorded_at,
            }
        )
        try:
            self._client.publish(self._channel, payload)
        except Exception as exc:
            log.warning("redis_publisher.error", error=str(exc))
