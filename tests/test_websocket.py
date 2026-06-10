"""WebSocket + Redis pub/sub integration test.

Spins up real Redis and Postgres testcontainers.  The Postgres container is
only needed to satisfy the API lifespan (async engine creation); no queries
are made against it from this test file.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def pg_url() -> str:  # type: ignore[return]
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        from app.common.config import Settings
        from app.common.db import run_migrations

        run_migrations(Settings(POSTGRES_SYNC_DSN=url))
        yield url


@pytest.fixture(scope="module")
def redis_url() -> str:  # type: ignore[return]
    from testcontainers.redis import RedisContainer

    with RedisContainer() as r:
        host = r.get_container_host_ip()
        port = r.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="module")
def ws_client(pg_url: str, redis_url: str) -> Any:  # type: ignore[return]
    from app.api.main import create_app
    from app.common.config import Settings

    settings = Settings(
        POSTGRES_DSN=pg_url.replace("+psycopg", "+asyncpg"),
        POSTGRES_SYNC_DSN=pg_url,
        REDIS_URL=redis_url,
        MINIO_ENDPOINT="localhost:9999",
        LOG_FORMAT="pretty",
    )
    _app = create_app(settings)
    with TestClient(_app) as client:
        yield client, redis_url


def test_websocket_receives_redis_event(ws_client: tuple[TestClient, str]) -> None:
    """Publish a notice event to Redis; assert it arrives at the WebSocket client."""
    client, redis_url = ws_client

    channel = "notice-events"
    event = {
        "event": "updated",
        "notice_id": "TEST/001",
        "diff": {"name": {"old": "Smith", "new": "Jones"}},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    payload = json.dumps(event)

    received: list[Any] = []

    def _publish() -> None:
        import redis as sync_redis

        time.sleep(0.3)  # allow the subscription to be established
        r = sync_redis.from_url(redis_url)
        r.publish(channel, payload)

    publisher = threading.Thread(target=_publish, daemon=True)
    publisher.start()

    with client.websocket_connect("/ws/alerts") as ws:
        data = ws.receive_json()
        received.append(data)

    publisher.join(timeout=5)

    assert len(received) == 1
    assert received[0]["event"] == "updated"
    assert received[0]["notice_id"] == "TEST/001"
    assert received[0]["diff"]["name"]["new"] == "Jones"
