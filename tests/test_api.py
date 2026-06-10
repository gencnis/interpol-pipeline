"""API integration tests.

Spins up Postgres + Redis via testcontainers.  MinIO is not required because
the test notices have no thumbnails (presigned URL code is skipped).  Runs
headlessly in CI — no docker-compose needed.
"""
from __future__ import annotations

import functools
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.common.config import Settings
from app.common.db import get_engine, make_session_factory, run_migrations, session_scope
from app.worker.normalizer import content_hash, normalize
from app.worker.repository import NoticeRepository

# ---------------------------------------------------------------------------
# Testcontainer fixtures (module-scoped → start once per module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_url() -> str:  # type: ignore[return]
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("+psycopg2", "+psycopg")


@pytest.fixture(scope="module")
def redis_url() -> str:  # type: ignore[return]
    from testcontainers.redis import RedisContainer

    with RedisContainer() as r:
        host = r.get_container_host_ip()
        port = r.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


_NOTICE: dict[str, Any] = {
    "notice_id": "1999/99999",
    "forename": "John",
    "name": "Doe",
    "sex_id": "M",
    "date_of_birth": "1970-01-01",
    "nationalities": ["US"],
    "arrest_warrant_countries": ["US"],
    "charge_text": "fraud",
    "thumbnail_url": None,
}


@pytest.fixture(scope="module")
def test_client(pg_url: str, redis_url: str) -> Any:  # type: ignore[return]
    """Build the FastAPI app wired to test containers and seed one notice."""
    from app.api.main import create_app

    sync_url = pg_url  # postgresql+psycopg://...
    async_url = pg_url.replace("+psycopg", "+asyncpg")

    settings = Settings(
        POSTGRES_DSN=async_url,
        POSTGRES_SYNC_DSN=sync_url,
        REDIS_URL=redis_url,
        MINIO_ENDPOINT="localhost:9999",  # unused — no thumbnails in test data
        LOG_FORMAT="pretty",
    )

    # Run Alembic migrations via sync engine.
    run_migrations(settings)

    # Seed one test notice.
    engine = get_engine(settings)
    factory = make_session_factory(engine)
    get_session = functools.partial(session_scope, factory)
    now = datetime.now(tz=UTC)
    norm = normalize(_NOTICE)
    hash_ = content_hash(norm)
    with get_session() as s:
        repo = NoticeRepository(s)
        if repo.get("1999/99999") is None:
            repo.create("1999/99999", norm, hash_, None, _NOTICE, now)
    engine.dispose()

    _app = create_app(settings)
    with TestClient(_app) as client:
        yield client


# ---------------------------------------------------------------------------
# Health / readiness
# ---------------------------------------------------------------------------


def test_healthz(test_client: TestClient) -> None:
    r = test_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz(test_client: TestClient) -> None:
    r = test_client.get("/readyz")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/notices
# ---------------------------------------------------------------------------


def test_notices_list_returns_200(test_client: TestClient) -> None:
    r = test_client.get("/api/notices")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert body["total"] >= 1
    assert body["page"] == 1


def test_notices_list_contains_seeded(test_client: TestClient) -> None:
    r = test_client.get("/api/notices")
    ids = [n["notice_id"] for n in r.json()["items"]]
    assert "1999/99999" in ids


def test_notices_filter_by_name_match(test_client: TestClient) -> None:
    r = test_client.get("/api/notices", params={"name": "Doe"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    names_lower = {
        ((n["name"] or "") + " " + (n["forename"] or "")).lower()
        for n in body["items"]
    }
    assert any("doe" in n for n in names_lower)


def test_notices_filter_by_name_nomatch(test_client: TestClient) -> None:
    r = test_client.get("/api/notices", params={"name": "XYZXYZ_NO_MATCH"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_notices_filter_by_status_active(test_client: TestClient) -> None:
    r = test_client.get("/api/notices", params={"status": "active"})
    assert r.status_code == 200
    assert all(n["status"] == "active" for n in r.json()["items"])


def test_notices_pagination(test_client: TestClient) -> None:
    r = test_client.get("/api/notices", params={"page": 1, "page_size": 1})
    body = r.json()
    assert len(body["items"]) <= 1
    assert body["page_size"] == 1


# ---------------------------------------------------------------------------
# GET /api/notices/{id}
# ---------------------------------------------------------------------------


def test_notice_detail_found(test_client: TestClient) -> None:
    r = test_client.get("/api/notices/1999/99999")
    assert r.status_code == 200
    body = r.json()
    assert body["notice_id"] == "1999/99999"
    assert body["name"] == "Doe"
    assert "history" in body
    assert len(body["history"]) >= 1
    assert body["history"][0]["change_type"] == "created"


def test_notice_detail_not_found(test_client: TestClient) -> None:
    r = test_client.get("/api/notices/9999/00000")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/alerts
# ---------------------------------------------------------------------------


def test_alerts_returns_200(test_client: TestClient) -> None:
    r = test_client.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "total" in body


def test_alerts_excludes_created(test_client: TestClient) -> None:
    # Seeded notice has only a 'created' history entry → alerts should be empty.
    r = test_client.get("/api/alerts")
    for item in r.json()["items"]:
        assert item["change_type"] in ("updated", "withdrawn")


# ---------------------------------------------------------------------------
# UI pages
# ---------------------------------------------------------------------------


def test_dashboard_html(test_client: TestClient) -> None:
    r = test_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Dashboard" in r.text


def test_notice_detail_html(test_client: TestClient) -> None:
    r = test_client.get("/notices/1999/99999")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Doe" in r.text


def test_notices_partial_htmx(test_client: TestClient) -> None:
    r = test_client.get("/partials/notices")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
