from __future__ import annotations

import json
from typing import Any

import redis

from app.common.logging import get_logger

log = get_logger(__name__)


class RedisPublisher:
    """Synchronous Redis publisher used by the worker process."""

    def __init__(self, settings: Any) -> None:
        self._client: redis.Redis = redis.from_url(settings.REDIS_URL)  # type: ignore[type-arg,unused-ignore]
        self._channel: str = settings.REDIS_EVENT_CHANNEL

    def publish(self, event: dict[str, Any]) -> None:
        try:
            self._client.publish(self._channel, json.dumps(event, default=str))
        except Exception as exc:
            log.warning("redis.publish_error", channel=self._channel, error=str(exc))

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
