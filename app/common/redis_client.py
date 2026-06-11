from __future__ import annotations

import json
from typing import Any

import redis as redis_lib

from app.common.logging import get_logger

log = get_logger(__name__)


class RedisPublisher:
    def __init__(self, settings: Any) -> None:
        self._client: redis_lib.Redis[str] = redis_lib.from_url(
            settings.REDIS_URL, decode_responses=True
        )
        self._channel: str = settings.REDIS_EVENT_CHANNEL

    def publish(self, event: dict[str, Any]) -> None:
        try:
            self._client.publish(self._channel, json.dumps(event, default=str))
        except Exception as exc:
            log.warning("redis.publish_failed", error=str(exc))

    def close(self) -> None:
        self._client.close()
