from __future__ import annotations

import json
from typing import Any

import redis as redis_sync

from app.common.logging import get_logger

log = get_logger(__name__)


class RedisPublisher:
    def __init__(self, redis_url: str, channel: str) -> None:
        self._r = redis_sync.from_url(redis_url)
        self._channel = channel

    def publish(self, event: dict[str, Any]) -> None:
        self._r.publish(self._channel, json.dumps(event, default=str))

    def close(self) -> None:
        self._r.close()
