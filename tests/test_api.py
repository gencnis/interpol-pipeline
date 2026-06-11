"""API endpoint tests for M4.

Uses httpx AsyncClient with ASGITransport so no network I/O is needed.
FastAPI dependencies are overridden to inject mocked DB / storage objects.
The httpx ASGITransport does not invoke the app lifespan, so no startup
patching is required.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixture: build an ASGI client with all heavy startup replaced by no-ops
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_mock_db_session() -> AsyncMock:
    """Return an AsyncSession mock whose execute returns sensible empty results."""
    session = AsyncMock()

    # scalar_one() → 0  (total count)
    count_result = MagicMock()
    count_result.scalar_one = MagicMock(return_value=0)
    count_result.scalar_one_or_none = MagicMock(return_value=None)

    # scalars().all() → []  (row list)
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[])

    paginated_result = MagicMock()
    paginated_result.scalars = MagicMock(return_value=scalars_mock)
    paginated_result.all = MagicMock(return_value=[])
    paginated_result.scalar_one = MagicMock(return_value=0)
    paginated_result.scalar_one_or_none = MagicMock(return_value=None)

    # First execute → count; subsequent executes → paginated rows.
    session.execute = AsyncMock(
        side_effect=[
            count_result,
            paginated_result,
            count_result,
            paginated_result,
        ]
    )

    return session


@pytest.fixture
async def client():  # type: ignore[return]
    """Async HTTP client against the FastAPI app with mocked dependencies."""
    from app.api.dependencies import get_db, get_storage
    from app.api.main import app

    async def mock_get_db() -> Any:  # type: ignore[return]
        session = _make_mock_db_session()
        yield session

    def mock_get_storage() -> MagicMock:
        storage = MagicMock()
        storage.get_presigned_url = MagicMock(return_value="http://minio/photo.jpg")
        return storage

    app.dependency_overrides[get_db] = mock_get_db
    app.dependency_overrides[get_storage] = mock_get_storage

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
async def client_not_found():  # type: ignore[return]
    """Client whose DB always returns None for scalar_one_or_none."""
    from app.api.dependencies import get_db, get_storage
    from app.api.main import app

    async def mock_get_db_none() -> Any:  # type: ignore[return]
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result)
        yield session

    def mock_get_storage() -> MagicMock:
        return MagicMock()

    app.dependency_overrides[get_db] = mock_get_db_none
    app.dependency_overrides[get_storage] = mock_get_storage

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.clear()


# ===========================================================================
# Health / readiness probes
# ===========================================================================

async def test_healthz(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ===========================================================================
# Notices API
# ===========================================================================

async def test_list_notices_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/notices")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert "page" in body
    assert "page_size" in body
    assert isinstance(body["items"], list)


async def test_list_notices_with_filters(client: AsyncClient) -> None:
    resp = await client.get("/api/notices?status=active&name=DOE")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body


async def test_list_notices_pagination_params(client: AsyncClient) -> None:
    resp = await client.get("/api/notices?page=2&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 2
    assert body["page_size"] == 10


async def test_get_notice_not_found(client_not_found: AsyncClient) -> None:
    resp = await client_not_found.get("/api/notices/2021/99999")
    assert resp.status_code == 404


# ===========================================================================
# Alerts API
# ===========================================================================

async def test_list_alerts_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)
    assert body["items"] == []


async def test_list_alerts_pagination_params(client: AsyncClient) -> None:
    resp = await client.get("/api/alerts?page=1&page_size=25")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 25


# ===========================================================================
# UI (HTML) routes
# ===========================================================================

async def test_dashboard_renders(client: AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "text/html" in ct


async def test_notice_detail_page_not_found(client_not_found: AsyncClient) -> None:
    """Notice detail page raises 404 when the DB has no matching row."""
    resp = await client_not_found.get("/notices/2021/99999")
    assert resp.status_code == 404
