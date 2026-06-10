"""Integration tests for the REST API endpoints.

Uses testcontainers Postgres, overrides FastAPI deps so no running
docker-compose is needed.  The async session factory talks to the same
Postgres that was seeded via the sync session factory.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api import deps
from app.api.main import app
from app.common.db import get_engine, make_session_factory, run_migrations, session_scope
from app.common.models import ChangeType, Notice, NoticeHistory, NoticeStatus

# ---------------------------------------------------------------------------
# Module-scoped testcontainer + sync infra fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_url() -> str:  # type: ignore[return]
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        yield url  # type: ignore[misc]


@pytest.fixture(scope="module")
def sync_engine(pg_url: str) -> Any:  # type: ignore[return]
    from app.common.config import Settings

    settings = Settings(
        POSTGRES_SYNC_DSN=pg_url,
        FETCH_NATIONALITIES=["TR"],
        FETCH_ARREST_WARRANT_COUNTRIES=["TR"],
    )
    eng = get_engine(settings)
    run_migrations(settings)
    yield eng  # type: ignore[misc]
    eng.dispose()


@pytest.fixture(scope="module")
def sync_session_factory(sync_engine: Any) -> Any:
    return make_session_factory(sync_engine)


@pytest.fixture(scope="module")
def async_pg_url(pg_url: str) -> str:
    """Convert sync psycopg URL to asyncpg URL."""
    return pg_url.replace("+psycopg", "+asyncpg").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


# ---------------------------------------------------------------------------
# Mock storage — always returns a dummy URL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_storage() -> MagicMock:
    s = MagicMock()
    s.get_presigned_url.return_value = "https://example.com/photo.jpg"
    return s


# ---------------------------------------------------------------------------
# Function-scoped async fixtures (avoid cross-loop issues)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_session_factory(
    async_pg_url: str,
    sync_engine: Any,  # ensures migrations run before we connect
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(async_pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(
    async_session_factory: async_sessionmaker[AsyncSession],
    mock_storage: MagicMock,
) -> AsyncGenerator[AsyncClient, None]:
    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as s:
            yield s

    from app.common.config import Settings

    test_settings = Settings(
        POSTGRES_SYNC_DSN="postgresql+psycopg://x:x@localhost/x",
        POSTGRES_DSN="postgresql+asyncpg://x:x@localhost/x",
        FETCH_NATIONALITIES=["TR"],
        FETCH_ARREST_WARRANT_COUNTRIES=["TR"],
    )

    app.dependency_overrides[deps.get_session] = _get_session
    app.dependency_overrides[deps.get_storage] = lambda: mock_storage
    app.dependency_overrides[deps.get_settings] = lambda: test_settings

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_notice(
    notice_id: str = "TEST/001",
    name: str = "Doe",
    forename: str = "Jane",
    status: str = NoticeStatus.active.value,
) -> Notice:
    now = datetime.now(tz=UTC)
    raw: dict[str, Any] = {"notice_id": notice_id, "name": name}
    return Notice(
        notice_id=notice_id,
        forename=forename,
        name=name,
        nationalities=["TR"],
        arrest_warrant_countries=["TR"],
        sex_id="F",
        date_of_birth="1990/01/01",
        content_hash=hashlib.sha256(json.dumps(raw).encode()).hexdigest(),
        status=status,
        raw_json=raw,
        first_seen_at=now,
        last_seen_at=now,
        last_changed_at=now,
    )


def _make_history(notice_id: str, change_type: str = ChangeType.updated.value) -> NoticeHistory:
    now = datetime.now(tz=UTC)
    return NoticeHistory(
        notice_id=notice_id,
        version=1,
        change_type=change_type,
        content_hash="abc123",
        snapshot={"notice_id": notice_id},
        diff={"name": {"old": "A", "new": "B"}},
        valid_from=now,
        recorded_at=now,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_healthz(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_list_notices_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/notices")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data
    assert data["page"] == 1


async def test_list_notices_with_data(
    client: AsyncClient, sync_session_factory: Any
) -> None:
    notice = _make_notice("LIST/001", name="Smith")
    with session_scope(sync_session_factory) as s:
        # Check if notice already exists to avoid PK conflict across reruns
        existing = s.get(Notice, "LIST/001")
        if existing is None:
            s.add(notice)

    resp = await client.get("/api/notices")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    ids = [item["notice_id"] for item in data["items"]]
    assert "LIST/001" in ids


async def test_list_notices_filter_by_name(
    client: AsyncClient, sync_session_factory: Any
) -> None:
    notice = _make_notice("FILTER/001", name="UniqueNameXYZ")
    with session_scope(sync_session_factory) as s:
        existing = s.get(Notice, "FILTER/001")
        if existing is None:
            s.add(notice)

    resp = await client.get("/api/notices", params={"name": "UniqueNameXYZ"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any("FILTER/001" == item["notice_id"] for item in data["items"])


async def test_get_notice_detail(
    client: AsyncClient, sync_session_factory: Any
) -> None:
    notice = _make_notice("DETAIL/001", name="DetailTest")
    with session_scope(sync_session_factory) as s:
        existing = s.get(Notice, "DETAIL/001")
        if existing is None:
            s.add(notice)

    # notice_id contains a slash — FastAPI uses {notice_id:path} to handle it
    resp = await client.get("/api/notices/DETAIL/001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["notice_id"] == "DETAIL/001"
    assert data["name"] == "DetailTest"
    assert "history" in data


async def test_get_notice_404(client: AsyncClient) -> None:
    resp = await client.get("/api/notices/NOTFOUND/999")
    assert resp.status_code == 404


async def test_list_alerts_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data


async def test_list_alerts_with_data(
    client: AsyncClient, sync_session_factory: Any
) -> None:
    # Insert parent notice first (FK constraint)
    notice = _make_notice("ALERT/001")
    with session_scope(sync_session_factory) as s:
        existing = s.get(Notice, "ALERT/001")
        if existing is None:
            s.add(notice)

    history = _make_history("ALERT/001", change_type=ChangeType.updated.value)
    with session_scope(sync_session_factory) as s:
        s.add(history)

    resp = await client.get("/api/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    notice_ids = [item["notice_id"] for item in data["items"]]
    assert "ALERT/001" in notice_ids
