"""WebSocket integration test.

Verifies that a Redis publish event arrives on a connected WebSocket client
by injecting a fake WebSocket into the ws_module._clients set and asserting
the message is received.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import redis as sync_redis

from app.api.routers import ws as ws_mod
from app.api.routers.ws import broadcast_redis_events

# ---------------------------------------------------------------------------
# Module-scoped Redis container fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def redis_container() -> str:  # type: ignore[return]
    try:
        from testcontainers.redis import RedisContainer

        with RedisContainer("redis:7-alpine") as r:
            host = r.get_container_host_ip()
            port = r.get_exposed_port(6379)
            yield f"redis://{host}:{port}/0"  # type: ignore[misc]
    except ImportError:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs

        with DockerContainer("redis:7-alpine").with_exposed_ports(6379) as c:
            wait_for_logs(c, "Ready to accept connections")
            host = c.get_container_host_ip()
            port = c.get_exposed_port(6379)
            yield f"redis://{host}:{port}/0"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_ws_receives_published_event(redis_container: str) -> None:
    test_channel = "test-events"
    ws_mod._clients.clear()

    # Start the background broadcast task
    task = asyncio.create_task(broadcast_redis_events(redis_container, test_channel))
    await asyncio.sleep(0.5)  # let subscriber connect

    # Register a fake WebSocket
    received: list[str] = []

    class _FakeWS:
        async def send_text(self, data: str) -> None:
            received.append(data)

    fake_ws = _FakeWS()
    ws_mod._clients.add(fake_ws)  # type: ignore[arg-type]

    # Publish event via sync redis
    r = sync_redis.from_url(redis_container)
    event = {
        "event": "updated",
        "notice_id": "TEST/001",
        "change_type": "updated",
        "diff": {"name": {"old": "A", "new": "B"}},
    }
    r.publish(test_channel, json.dumps(event))
    r.close()

    await asyncio.sleep(0.5)  # let the broadcast task process it

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Clean up
    ws_mod._clients.discard(fake_ws)  # type: ignore[arg-type]

    assert len(received) >= 1, f"Expected at least 1 WS message, got: {received}"
    msg = json.loads(received[0])
    assert msg["notice_id"] == "TEST/001"
    assert msg["change_type"] == "updated"
