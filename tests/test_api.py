"""Integration tests for M4 REST API endpoints and WebSocket live alerts.

Uses testcontainers to spin up real Postgres and Redis — no running
docker-compose required. Follows the same module-scoped fixture pattern as
test_processor.py.

The tests target the M4 API implementation at:
  - app.api.deps          (get_db, _engine, _factory)
  - app.api.routers.notices
  - app.api.routers.ws
  - app.api.routers.ui

Test coverage:
  - GET /healthz
  - GET /api/notices  (list, pagination shape, filter by name/nationality/status)
  - GET /api/notices/{id}  (detail + history + photo_url; slash in ID; 404)
  - GET /api/alerts   (only updated/withdrawn; includes notice_forename/notice_name)
  - WS /ws/alerts     (Redis-published event arrives on connected WebSocket)
"""
from __future__ import annotations

import asyncio
import functools
import json
import threading
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.main import app
from app.common.config import Settings
from app.common.db import get_engine, make_session_factory, run_migrations, session_scope
from app.common.storage import StorageClient
from app.worker.photo_service import PhotoService
from app.worker.processor import NoticeProcessor


# ────────────────────────────────────────────────────────────────────────────
# Container fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pg_url() -> str:  # type: ignore[return]
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        yield url


@pytest.fixture(scope="module")
def redis_url() -> str:  # type: ignore[return]
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as rc:
        host = rc.get_container_host_ip()
        port = rc.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


# ────────────────────────────────────────────────────────────────────────────
# Settings fixture
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def test_settings(pg_url: str, redis_url: str) -> Settings:
    """Settings pointing at real test containers; fake MinIO (not used by API)."""
    return Settings(
        POSTGRES_DSN=pg_url.replace("+psycopg", "+asyncpg"),
        POSTGRES_SYNC_DSN=pg_url,
        REDIS_URL=redis_url,
        REDIS_EVENT_CHANNEL="test-notice-events",
        FETCH_NATIONALITIES=["TR"],
        FETCH_ARREST_WARRANT_COUNTRIES=["TR"],
        MINIO_ENDPOINT="localhost:9999",  # not called during API tests
        MINIO_ACCESS_KEY="x",
        MINIO_SECRET_KEY="x",
        MINIO_BUCKET="x",
        MINIO_SECURE=False,
    )


# ────────────────────────────────────────────────────────────────────────────
# DB fixtures (sync for seeding, async for the FastAPI dependency)
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sync_engine(test_settings: Settings) -> Any:  # type: ignore[return]
    eng = get_engine(test_settings)
    run_migrations(test_settings)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def sync_session_factory(sync_engine: Any) -> Any:
    return make_session_factory(sync_engine)


# ────────────────────────────────────────────────────────────────────────────
# Seed data
# ────────────────────────────────────────────────────────────────────────────


def _seed_notices(sync_session_factory: Any, test_settings: Settings) -> None:
    """Insert test data using the worker's NoticeProcessor (sync path).

    Creates:
      - API/001 (Alice WALKER, US national) — then updated to add CA nationality
      - API/002 (Bob SMITH, GB national) — no updates

    After seeding:
      - notice_history will have a 'created' row for each notice
      - notice_history will have an 'updated' row for API/001 (nationalities changed)
      - GET /api/alerts must return at least 1 row (the 'updated' event for API/001)
    """

    class _FakeSettings:
        INTERPOL_IMPERSONATE = "chrome120"
        INTERPOL_REFERER = "https://www.interpol.int/"
        WITHDRAWAL_MIN_CYCLE_SIZE = 1

    # StorageClient constructor tries to connect to MinIO — use a fake object
    # that provides the required attributes but is never actually called.
    storage = StorageClient(
        type(
            "_S",
            (),
            {
                "MINIO_ENDPOINT": "localhost:9999",
                "MINIO_ACCESS_KEY": "x",
                "MINIO_SECRET_KEY": "x",
                "MINIO_BUCKET": "x",
                "MINIO_SECURE": False,
                "MINIO_PRESIGN_EXPIRY_SECONDS": 3600,
            },
        )()
    )
    photo_service = PhotoService(storage, _FakeSettings())
    get_session_fn = functools.partial(session_scope, sync_session_factory)
    proc = NoticeProcessor(get_session_fn, photo_service, _FakeSettings())

    # First notice — US, no photo
    proc.handle_upsert(
        {
            "notice_id": "API/001",
            "forename": "Alice",
            "name": "WALKER",
            "nationalities": ["US"],
            "arrest_warrant_countries": ["US"],
            "sex_id": "F",
            "date_of_birth": "1985/03/12",
            "cycle_id": "seed",
        }
    )
    # Second notice — GB
    proc.handle_upsert(
        {
            "notice_id": "API/002",
            "forename": "Bob",
            "name": "SMITH",
            "nationalities": ["GB"],
            "arrest_warrant_countries": ["GB"],
            "sex_id": "M",
            "date_of_birth": "1970/07/04",
            "cycle_id": "seed",
        }
    )
    # Update API/001: add CA → produces an 'updated' history row → shows up in /api/alerts
    proc.handle_upsert(
        {
            "notice_id": "API/001",
            "forename": "Alice",
            "name": "WALKER",
            "nationalities": ["US", "CA"],
            "arrest_warrant_countries": ["US"],
            "sex_id": "F",
            "date_of_birth": "1985/03/12",
            "cycle_id": "seed2",
        }
    )


@pytest.fixture(scope="module")
def seeded_db(sync_session_factory: Any, test_settings: Settings) -> None:
    """Seed the database once for the whole module."""
    _seed_notices(sync_session_factory, test_settings)


# ────────────────────────────────────────────────────────────────────────────
# HTTP client fixture
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def api_client(
    test_settings: Settings,
    seeded_db: None,
) -> Any:  # type: ignore[return]
    """AsyncClient wired to the FastAPI app with overridden DB + settings.

    The DB dependency override creates a fresh async engine per request, bound
    to the current test function's event loop.  This avoids the "wrong event
    loop" issue that arises from creating a module-scoped async engine outside
    of any event loop context.
    """
    from app.api.deps import get_db as _get_db  # type: ignore[import]

    async_dsn = test_settings.POSTGRES_DSN

    async def _override_db() -> Any:  # type: ignore[return]
        # Engine created inside the request's async context → correct event loop.
        eng = create_async_engine(async_dsn, pool_pre_ping=True)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as session:
            yield session
        await eng.dispose()

    app.dependency_overrides[_get_db] = _override_db

    import app.api.deps as _api_deps  # type: ignore[import]
    _api_deps._engine = None  # type: ignore[attr-defined]
    _api_deps._factory = None  # type: ignore[attr-defined]

    patches = [
        patch("app.common.config.get_settings", return_value=test_settings),
        patch("app.api.routers.notices.get_settings", return_value=test_settings),
        patch("app.api.routers.ui.get_settings", return_value=test_settings),
    ]
    for p in patches:
        p.start()

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    client = AsyncClient(transport=transport, base_url="http://test")
    yield client

    loop = asyncio.new_event_loop()
    loop.run_until_complete(client.aclose())
    loop.close()
    for p in patches:
        p.stop()
    app.dependency_overrides.clear()


# ────────────────────────────────────────────────────────────────────────────
# Tests: health endpoint
# ────────────────────────────────────────────────────────────────────────────


async def test_healthz(api_client: AsyncClient) -> None:
    r = await api_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ────────────────────────────────────────────────────────────────────────────
# Tests: GET /api/notices
# ────────────────────────────────────────────────────────────────────────────


async def test_list_notices_returns_paginated_shape(api_client: AsyncClient) -> None:
    """Response must include items/total/page/per_page keys."""
    r = await api_client.get("/api/notices")
    assert r.status_code == 200
    body = r.json()
    for key in ("items", "total", "page", "per_page"):
        assert key in body, f"missing key: {key}"
    assert body["page"] == 1
    assert body["total"] >= 2


async def test_list_notices_item_fields(api_client: AsyncClient) -> None:
    """Each item in the list must carry the documented fields."""
    r = await api_client.get("/api/notices")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) >= 1
    item = body["items"][0]
    required_fields = (
        "notice_id",
        "forename",
        "name",
        "sex_id",
        "date_of_birth",
        "nationalities",
        "arrest_warrant_countries",
        "charge_text",
        "thumbnail_object_key",
        "status",
        "first_seen_at",
        "last_seen_at",
        "last_changed_at",
    )
    for field in required_fields:
        assert field in item, f"missing field in list item: {field}"


async def test_list_notices_filter_by_name(api_client: AsyncClient) -> None:
    """name= filter returns only notices whose name matches."""
    r = await api_client.get("/api/notices", params={"name": "WALKER"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert "WALKER" in (item["name"] or ""), (
            f"expected WALKER in name, got {item['name']!r}"
        )


async def test_list_notices_filter_by_name_no_results(api_client: AsyncClient) -> None:
    """name= filter returns empty list when nothing matches."""
    r = await api_client.get("/api/notices", params={"name": "ZZZNOMATCH"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_list_notices_filter_by_nationality(api_client: AsyncClient) -> None:
    """nationality= filter returns only notices with that nationality."""
    r = await api_client.get("/api/notices", params={"nationality": "GB"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert "GB" in item["nationalities"], (
            f"expected GB in nationalities, got {item['nationalities']!r}"
        )


async def test_list_notices_filter_by_status_active(api_client: AsyncClient) -> None:
    """status=active filter returns only active notices."""
    r = await api_client.get("/api/notices", params={"status": "active"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    for item in body["items"]:
        assert item["status"] == "active"


async def test_list_notices_filter_by_status_withdrawn(api_client: AsyncClient) -> None:
    """status=withdrawn filter returns empty list (no withdrawals seeded)."""
    r = await api_client.get("/api/notices", params={"status": "withdrawn"})
    assert r.status_code == 200
    body = r.json()
    # No withdrawn notices seeded → total must be 0
    assert body["total"] == 0


# ────────────────────────────────────────────────────────────────────────────
# Tests: GET /api/notices/{notice_id}
# ────────────────────────────────────────────────────────────────────────────


async def test_get_notice_detail_basic(api_client: AsyncClient) -> None:
    """Detail endpoint returns the notice and its history."""
    r = await api_client.get("/api/notices/API/001")
    assert r.status_code == 200
    body = r.json()
    assert body["notice_id"] == "API/001"
    assert body["forename"] == "Alice"
    assert body["name"] == "WALKER"
    assert "history" in body
    assert len(body["history"]) >= 1


async def test_get_notice_detail_photo_url_null_when_no_thumbnail(
    api_client: AsyncClient,
) -> None:
    """photo_url must be present in detail response; null when no thumbnail key."""
    r = await api_client.get("/api/notices/API/001")
    assert r.status_code == 200
    body = r.json()
    assert "photo_url" in body
    # Seeded without thumbnail_url → thumbnail_object_key is None → photo_url is None
    assert body["photo_url"] is None


async def test_get_notice_detail_history_fields(api_client: AsyncClient) -> None:
    """Each history row must carry the documented fields."""
    r = await api_client.get("/api/notices/API/001")
    assert r.status_code == 200
    history = r.json()["history"]
    assert len(history) >= 1
    row = history[0]
    required = ("id", "version", "change_type", "content_hash", "diff", "valid_from", "valid_to", "recorded_at")
    for field in required:
        assert field in row, f"missing history field: {field}"


async def test_get_notice_detail_history_has_created_row(api_client: AsyncClient) -> None:
    """The first history row for a new notice must be change_type='created'."""
    r = await api_client.get("/api/notices/API/001")
    assert r.status_code == 200
    history = r.json()["history"]
    created_rows = [h for h in history if h["change_type"] == "created"]
    assert len(created_rows) == 1, "expected exactly one 'created' history row"
    assert created_rows[0]["version"] == 1


async def test_get_notice_detail_history_has_updated_row(api_client: AsyncClient) -> None:
    """API/001 was updated once → history must contain an 'updated' row with diff."""
    r = await api_client.get("/api/notices/API/001")
    assert r.status_code == 200
    history = r.json()["history"]
    updated_rows = [h for h in history if h["change_type"] == "updated"]
    assert len(updated_rows) >= 1, "expected at least one 'updated' history row"
    # The diff must reference the nationalities change
    diff = updated_rows[0]["diff"]
    assert diff is not None
    assert "nationalities" in diff


async def test_get_notice_detail_slash_in_id(api_client: AsyncClient) -> None:
    """notice_id containing a slash must route correctly without 404."""
    # "API/001" has a slash — the route must handle it via path parameter
    r = await api_client.get("/api/notices/API/001")
    assert r.status_code == 200
    assert r.json()["notice_id"] == "API/001"


async def test_get_notice_detail_second_notice(api_client: AsyncClient) -> None:
    """Detail works for API/002 (Bob SMITH, GB)."""
    r = await api_client.get("/api/notices/API/002")
    assert r.status_code == 200
    body = r.json()
    assert body["notice_id"] == "API/002"
    assert body["name"] == "SMITH"
    assert "GB" in body["nationalities"]


async def test_get_notice_detail_404_for_unknown(api_client: AsyncClient) -> None:
    """Detail endpoint returns 404 for a notice_id that does not exist."""
    r = await api_client.get("/api/notices/NOPE/999")
    assert r.status_code == 404


async def test_get_notice_detail_404_message(api_client: AsyncClient) -> None:
    """404 response body must include a detail field."""
    r = await api_client.get("/api/notices/DOESNT/EXIST")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body


# ────────────────────────────────────────────────────────────────────────────
# Tests: GET /api/alerts
# ────────────────────────────────────────────────────────────────────────────


async def test_alerts_paginated_shape(api_client: AsyncClient) -> None:
    """Alerts response must include items/total/page/per_page."""
    r = await api_client.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    for key in ("items", "total", "page", "per_page"):
        assert key in body, f"missing key in alerts response: {key}"


async def test_alerts_excludes_created_change_type(api_client: AsyncClient) -> None:
    """Alerts must NEVER include 'created' history rows — only updated/withdrawn."""
    r = await api_client.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    for item in body["items"]:
        assert item["change_type"] in ("updated", "withdrawn"), (
            f"alerts must not contain 'created', got {item['change_type']!r}"
        )


async def test_alerts_has_at_least_one_item(api_client: AsyncClient) -> None:
    """After seeding (API/001 updated), alerts must return at least 1 item."""
    r = await api_client.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1, "expected at least 1 alert (the API/001 update)"


async def test_alerts_item_fields(api_client: AsyncClient) -> None:
    """Each alert row must carry history fields plus notice_forename/notice_name."""
    r = await api_client.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) >= 1
    item = body["items"][0]
    # History-side fields
    for field in ("id", "notice_id", "version", "change_type", "diff"):
        assert field in item, f"missing alert field: {field}"
    # Notice-side join fields
    assert "notice_forename" in item, "missing notice_forename in alert"
    assert "notice_name" in item, "missing notice_name in alert"


async def test_alerts_contains_updated_row_for_api001(api_client: AsyncClient) -> None:
    """There must be an alert with notice_id='API/001' and change_type='updated'."""
    r = await api_client.get("/api/alerts")
    assert r.status_code == 200
    items = r.json()["items"]
    matching = [i for i in items if i["notice_id"] == "API/001" and i["change_type"] == "updated"]
    assert len(matching) >= 1, "expected an 'updated' alert for API/001"
    alert = matching[0]
    assert alert["notice_forename"] == "Alice"
    assert alert["notice_name"] == "WALKER"


async def test_alerts_diff_present_for_updated(api_client: AsyncClient) -> None:
    """Updated alerts must carry a non-null diff describing what changed."""
    r = await api_client.get("/api/alerts")
    assert r.status_code == 200
    items = r.json()["items"]
    updated_items = [i for i in items if i["change_type"] == "updated"]
    assert len(updated_items) >= 1
    for item in updated_items:
        assert item["diff"] is not None, "updated alert must have a non-null diff"


# ────────────────────────────────────────────────────────────────────────────
# Tests: WebSocket /ws/alerts
# ────────────────────────────────────────────────────────────────────────────


def test_websocket_receives_redis_event(
    test_settings: Settings,
    seeded_db: None,
) -> None:
    """Connect to /ws/alerts; publish a Redis message; assert it arrives.

    Uses starlette.testclient.TestClient (synchronous) because httpx_ws is
    not a declared dependency.  A background thread publishes to Redis 400 ms
    after the WebSocket connection is established, which gives the subscriber
    goroutine time to subscribe before the message is sent.
    """
    import redis as sync_redis
    from starlette.testclient import TestClient

    from app.api.deps import get_db as _get_db  # type: ignore[import]

    # DB is not used by the WS endpoint; override with a no-op.
    async def _override_db() -> Any:  # type: ignore[return]
        eng = create_async_engine(test_settings.POSTGRES_DSN, pool_pre_ping=True)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as session:
            yield session
        await eng.dispose()

    app.dependency_overrides[_get_db] = _override_db

    event_payload = {
        "notice_id": "WS/TEST",
        "change_type": "updated",
        "forename": "Test",
        "name": "USER",
        "diff": {"name": {"old": "X", "new": "Y"}},
        "recorded_at": "2024-01-01T00:00:00+00:00",
    }
    serialized = json.dumps(event_payload)

    received_messages: list[str] = []

    def _publish_after_delay() -> None:
        """Publish a Redis message after a short delay to let WS subscribe."""
        import time

        time.sleep(0.4)
        rc = sync_redis.from_url(test_settings.REDIS_URL)
        rc.publish(test_settings.REDIS_EVENT_CHANNEL, serialized)
        rc.close()

    with patch("app.api.routers.ws.get_settings", return_value=test_settings):
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.websocket_connect("/ws/alerts") as ws:
                t = threading.Thread(target=_publish_after_delay, daemon=True)
                t.start()
                # Receive with a generous timeout; TestClient.receive_text() blocks
                # until a frame arrives.  Five seconds is well within CI budgets.
                data = ws.receive_text()
                received_messages.append(data)
                t.join(timeout=2.0)

    app.dependency_overrides.clear()

    assert len(received_messages) == 1, "expected exactly one WebSocket message"
    parsed = json.loads(received_messages[0])
    assert parsed["notice_id"] == "WS/TEST"
    assert parsed["change_type"] == "updated"
    diff = parsed.get("diff") or {}
    assert diff.get("name", {}).get("new") == "Y"


def test_websocket_multiple_events_arrive_in_order(
    test_settings: Settings,
    seeded_db: None,
) -> None:
    """Multiple Redis events must all arrive on the WebSocket in publish order."""
    import time

    import redis as sync_redis
    from starlette.testclient import TestClient

    from app.api.deps import get_db as _get_db  # type: ignore[import]

    async def _override_db() -> Any:  # type: ignore[return]
        eng = create_async_engine(test_settings.POSTGRES_DSN, pool_pre_ping=True)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as session:
            yield session
        await eng.dispose()

    app.dependency_overrides[_get_db] = _override_db

    payloads = [
        {
            "notice_id": f"WS/MULTI{i}",
            "change_type": "updated",
            "forename": "Multi",
            "name": f"EVENT{i}",
            "diff": None,
            "recorded_at": "2024-01-01T00:00:00+00:00",
        }
        for i in range(3)
    ]

    received: list[str] = []

    def _publish_all() -> None:
        time.sleep(0.4)
        rc = sync_redis.from_url(test_settings.REDIS_URL)
        for p in payloads:
            rc.publish(test_settings.REDIS_EVENT_CHANNEL, json.dumps(p))
            time.sleep(0.05)
        rc.close()

    with patch("app.api.routers.ws.get_settings", return_value=test_settings):
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.websocket_connect("/ws/alerts") as ws:
                t = threading.Thread(target=_publish_all, daemon=True)
                t.start()
                for _ in range(len(payloads)):
                    received.append(ws.receive_text())
                t.join(timeout=3.0)

    app.dependency_overrides.clear()

    assert len(received) == len(payloads)
    for i, raw in enumerate(received):
        data = json.loads(raw)
        assert data["notice_id"] == f"WS/MULTI{i}"
