"""WebSocket tests for M4.

Tests the /ws/alerts endpoint by mocking the Redis pub/sub connection so
no real Redis instance is needed.  The heavy startup (DB migrations, MinIO)
is bypassed by patching the three callables that _lifespan invokes at boot.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers: patches that suppress the lifespan infrastructure calls
# ---------------------------------------------------------------------------

def _lifespan_patches():
    """Return a list of context managers that suppress the startup I/O."""
    return [
        patch("app.api.main.run_migrations"),
        patch("app.api.main.get_async_engine", return_value=MagicMock()),
        patch("app.api.main.get_async_session_factory", return_value=MagicMock()),
        patch("app.common.storage.StorageClient.ensure_bucket"),
        # StorageClient.__init__ talks to MinIO — replace the whole constructor
        patch(
            "app.api.main.StorageClient",
            return_value=MagicMock(spec_set=["ensure_bucket", "get_presigned_url"]),
        ),
        # Async engine.dispose must be awaitable
        patch("app.api.db.AsyncEngine.dispose", new_callable=AsyncMock),
    ]


# ---------------------------------------------------------------------------
# Shared fake Redis pubsub factory
# ---------------------------------------------------------------------------

def _make_redis_mock(fake_listen):  # type: ignore[return]
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.listen = fake_listen
    mock_pubsub.unsubscribe = AsyncMock()

    mock_client = AsyncMock()
    mock_client.pubsub = MagicMock(return_value=mock_pubsub)
    mock_client.aclose = AsyncMock()
    return mock_client


# ===========================================================================
# Tests
# ===========================================================================

def test_ws_alerts_connection() -> None:
    """WebSocket /ws/alerts accepts a connection and streams Redis pub/sub events."""
    from starlette.testclient import TestClient

    from app.api.main import app

    _sample_event = {
        "event": "updated",
        "notice_id": "2021/1",
        "notice_name": "DOE John",
        "diff": None,
        "recorded_at": "2024-01-01T00:00:00+00:00",
    }
    _sample_bytes = json.dumps(_sample_event).encode()

    async def fake_listen():  # type: ignore[return]
        yield {"type": "message", "data": _sample_bytes}
        raise Exception("stop")

    mock_redis_client = _make_redis_mock(fake_listen)

    with (
        patch("app.api.main.run_migrations"),
        patch("app.api.main.get_async_engine", return_value=AsyncMock()),
        patch("app.api.main.get_async_session_factory", return_value=MagicMock()),
        patch("app.api.main.StorageClient") as MockStorage,
        patch("redis.asyncio.from_url", return_value=mock_redis_client),
    ):
        mock_storage_instance = MagicMock()
        mock_storage_instance.ensure_bucket = MagicMock()
        MockStorage.return_value = mock_storage_instance

        with TestClient(app, raise_server_exceptions=False) as test_client:
            with test_client.websocket_connect("/ws/alerts") as ws:
                raw = ws.receive_text()
                msg = json.loads(raw)
                assert msg["event"] == "updated"
                assert "notice_id" in msg
                assert msg["notice_id"] == "2021/1"


def test_ws_alerts_handles_bytes_data() -> None:
    """WebSocket decodes bytes messages from Redis correctly."""
    from starlette.testclient import TestClient

    from app.api.main import app

    _event = {"event": "withdrawn", "notice_id": "2022/42", "notice_name": "SMITH"}
    _event_bytes = json.dumps(_event).encode()

    async def fake_listen():  # type: ignore[return]
        yield {"type": "message", "data": _event_bytes}
        raise Exception("stop")

    mock_redis_client = _make_redis_mock(fake_listen)

    with (
        patch("app.api.main.run_migrations"),
        patch("app.api.main.get_async_engine", return_value=AsyncMock()),
        patch("app.api.main.get_async_session_factory", return_value=MagicMock()),
        patch("app.api.main.StorageClient") as MockStorage,
        patch("redis.asyncio.from_url", return_value=mock_redis_client),
    ):
        mock_storage_instance = MagicMock()
        mock_storage_instance.ensure_bucket = MagicMock()
        MockStorage.return_value = mock_storage_instance

        with TestClient(app, raise_server_exceptions=False) as test_client:
            with test_client.websocket_connect("/ws/alerts") as ws:
                raw = ws.receive_text()
                msg = json.loads(raw)
                assert msg["event"] == "withdrawn"
                assert msg["notice_id"] == "2022/42"


def test_ws_alerts_skips_non_message_types() -> None:
    """WebSocket silently ignores subscribe confirmation messages from Redis."""
    from starlette.testclient import TestClient

    from app.api.main import app

    _real_event = {"event": "updated", "notice_id": "2023/99"}
    _real_bytes = json.dumps(_real_event).encode()

    async def fake_listen():  # type: ignore[return]
        # First: a subscribe confirmation (should be ignored by handler)
        yield {"type": "subscribe", "data": 1}
        # Second: the real message
        yield {"type": "message", "data": _real_bytes}
        raise Exception("stop")

    mock_redis_client = _make_redis_mock(fake_listen)

    with (
        patch("app.api.main.run_migrations"),
        patch("app.api.main.get_async_engine", return_value=AsyncMock()),
        patch("app.api.main.get_async_session_factory", return_value=MagicMock()),
        patch("app.api.main.StorageClient") as MockStorage,
        patch("redis.asyncio.from_url", return_value=mock_redis_client),
    ):
        mock_storage_instance = MagicMock()
        mock_storage_instance.ensure_bucket = MagicMock()
        MockStorage.return_value = mock_storage_instance

        with TestClient(app, raise_server_exceptions=False) as test_client:
            with test_client.websocket_connect("/ws/alerts") as ws:
                raw = ws.receive_text()
                msg = json.loads(raw)
                assert msg["notice_id"] == "2023/99"
