"""Integration tests for NoticeProcessor.

Uses testcontainers to spin up real Postgres and MinIO — no running
docker-compose required.  Runs headlessly in CI (GitHub Actions ubuntu-latest
has Docker available) and locally via `make test` (Docker socket mounted).

Each test class method runs in scenario order (a → e) so that state from
earlier steps is available to later ones.  The module-scoped fixtures
create fresh containers and run migrations once per test session.
"""
from __future__ import annotations

import functools
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text

from app.common.db import get_engine, make_session_factory, run_migrations, session_scope
from app.common.models import ChangeType, Notice, NoticeHistory, NoticeStatus
from app.common.storage import StorageClient
from app.worker.photo_service import PhotoService
from app.worker.processor import NoticeProcessor

# ---------------------------------------------------------------------------
# Minimal JPEG bytes — avoids any real photo download in tests
# ---------------------------------------------------------------------------
_FAKE_JPEG = bytes(
    [0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46,
     0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xD9]
)

# ---------------------------------------------------------------------------
# testcontainers fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pg_url() -> str:  # type: ignore[return]
    from testcontainers.postgres import PostgresContainer
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        yield url


@pytest.fixture(scope="module")
def minio_endpoint() -> str:  # type: ignore[return]
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    with (
        DockerContainer("minio/minio:latest")
        .with_exposed_ports(9000)
        .with_env("MINIO_ROOT_USER", "minioadmin")
        .with_env("MINIO_ROOT_PASSWORD", "minioadmin")
        .with_command("server /data")
    ) as container:
        wait_for_logs(container, "API:", timeout=30)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        yield f"{host}:{port}"


# ---------------------------------------------------------------------------
# App-level fixtures wired from containers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine(pg_url: str) -> Any:  # type: ignore[return]
    from app.common.config import Settings

    settings = Settings(
        POSTGRES_SYNC_DSN=pg_url,
        FETCH_NATIONALITIES=["TR"],
        FETCH_ARREST_WARRANT_COUNTRIES=["TR"],
    )
    eng = get_engine(settings)
    run_migrations(settings)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def session_factory(engine: Any) -> Any:
    return make_session_factory(engine)


@pytest.fixture(scope="module")
def storage(minio_endpoint: str) -> StorageClient:
    sc = StorageClient.from_params(
        endpoint=minio_endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="test-photos",
    )
    sc.ensure_bucket()
    return sc


@pytest.fixture(scope="module")
def processor(session_factory: Any, storage: StorageClient) -> NoticeProcessor:
    """Processor with photo downloads mocked to return _FAKE_JPEG."""

    class _FakeSettings:
        INTERPOL_IMPERSONATE = "chrome120"
        INTERPOL_REFERER = "https://www.interpol.int/"
        WITHDRAWAL_MIN_CYCLE_SIZE = 1  # allow small test cycles

    photo_service = PhotoService(storage, _FakeSettings())
    get_session = functools.partial(session_scope, session_factory)
    return NoticeProcessor(get_session, photo_service, _FakeSettings())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(
    notice_id: str = "TEST/001",
    name: str = "Doe",
    nationalities: list[str] | None = None,
    thumbnail_url: str | None = None,
    cycle_id: str = "cycle-test",
) -> dict[str, Any]:
    return {
        "notice_id": notice_id,
        "forename": "Jane",
        "name": name,
        "nationalities": nationalities or ["TR"],
        "arrest_warrant_countries": ["TR"],
        "sex_id": "F",
        "date_of_birth": "1990/01/01",
        "thumbnail_url": thumbnail_url,
        "cycle_id": cycle_id,
    }


def _get_notice(session_factory: Any, notice_id: str) -> Notice | None:
    with session_scope(session_factory) as s:
        notice = s.get(Notice, notice_id)
        if notice is not None:
            # Expunge before commit so the object isn't expired by commit and
            # can be read after the session closes.
            s.expunge(notice)
        return notice


def _get_history(session_factory: Any, notice_id: str) -> list[NoticeHistory]:
    with session_scope(session_factory) as s:
        rows = list(
            s.scalars(
                select(NoticeHistory)
                .where(NoticeHistory.notice_id == notice_id)
                .order_by(NoticeHistory.version)
            )
        )
        for row in rows:
            s.expunge(row)
        return rows


# ===========================================================================
# (a) New notice → inserted, history row with change_type='created'
# ===========================================================================

def test_a_new_notice_creates_row_and_history(
    processor: NoticeProcessor,
    session_factory: Any,
) -> None:
    result = processor.handle_upsert(_payload("TEST/001"))
    assert result == "created"

    notice = _get_notice(session_factory, "TEST/001")
    assert notice is not None, "notices row must exist"
    assert notice.name == "Doe"
    assert notice.nationalities == ["TR"]
    assert notice.status == NoticeStatus.active.value
    assert notice.content_hash  # non-empty

    history = _get_history(session_factory, "TEST/001")
    assert len(history) == 1, f"expected 1 history row, got {len(history)}"
    h = history[0]
    assert h.version == 1
    assert h.change_type == ChangeType.created.value
    assert h.diff is None
    assert h.valid_to is None  # still the current version

    print(f"\n[a] notices row: notice_id={notice.notice_id!r} status={notice.status!r} "
          f"hash={notice.content_hash[:12]}…")
    print(f"[a] history row: version={h.version} change_type={h.change_type!r} "
          f"valid_to={h.valid_to!r}")


# ===========================================================================
# (b) Same notice again → noop, no new history row, last_seen_at updated
# ===========================================================================

def test_b_same_notice_is_noop(
    processor: NoticeProcessor,
    session_factory: Any,
) -> None:
    # Touch last_seen_at so we can verify it moved.
    # Access last_seen_at while inside the session so it's loaded before close.
    with session_scope(session_factory) as s:
        notice = s.get(Notice, "TEST/001")
        assert notice is not None
        before_ts: Any = notice.last_seen_at

    time.sleep(0.05)  # ensure clock advances
    result = processor.handle_upsert(_payload("TEST/001"))
    assert result == "noop"

    history = _get_history(session_factory, "TEST/001")
    assert len(history) == 1, "noop must NOT add a history row"

    notice = _get_notice(session_factory, "TEST/001")
    assert notice is not None
    assert notice.last_seen_at > before_ts, "last_seen_at must advance on noop"

    print(f"\n[b] history rows still: {len(history)} — correct")
    print(f"[b] last_seen_at advanced: {before_ts} → {notice.last_seen_at}")


# ===========================================================================
# (c) Changed notice → updated with correct field-level diff
# ===========================================================================

def test_c_changed_notice_has_diff(
    processor: NoticeProcessor,
    session_factory: Any,
) -> None:
    result = processor.handle_upsert(
        _payload("TEST/001", nationalities=["TR", "US"])
    )
    assert result == "updated"

    notice = _get_notice(session_factory, "TEST/001")
    assert notice is not None
    assert notice.nationalities == ["TR", "US"]

    history = _get_history(session_factory, "TEST/001")
    assert len(history) == 2, f"expected 2 history rows, got {len(history)}"

    h_created = history[0]
    h_updated = history[1]

    assert h_created.change_type == ChangeType.created.value
    assert h_created.valid_to is not None, "prior row must be closed"

    assert h_updated.change_type == ChangeType.updated.value
    assert h_updated.version == 2
    assert h_updated.diff is not None
    assert "nationalities" in h_updated.diff, (
        f"diff must contain 'nationalities', got: {h_updated.diff}"
    )
    assert h_updated.diff["nationalities"]["old"] == ["TR"]
    assert sorted(h_updated.diff["nationalities"]["new"]) == ["TR", "US"]
    assert h_updated.valid_to is None  # this is now the current version

    print(f"\n[c] diff: {h_updated.diff}")
    print(f"[c] history[0].valid_to = {h_created.valid_to}  (closed)")
    print(f"[c] history[1].valid_to = {h_updated.valid_to}  (open / current)")


# ===========================================================================
# (d) Photo uploaded to MinIO; notices row stores the object key
# ===========================================================================

def test_d_photo_stored_in_minio(
    processor: NoticeProcessor,
    session_factory: Any,
    storage: StorageClient,
) -> None:
    with patch("app.worker.photo_service.CffiSession") as mock_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = _FAKE_JPEG
        mock_cls.return_value.__enter__.return_value.get.return_value = mock_resp

        result = processor.handle_upsert(
            _payload("TEST/002", thumbnail_url="https://example.com/photo.jpg")
        )

    assert result == "created"

    notice = _get_notice(session_factory, "TEST/002")
    assert notice is not None
    obj_key = notice.thumbnail_object_key
    assert obj_key is not None, "thumbnail_object_key must be set"
    assert obj_key.startswith("red/TEST_002/"), f"unexpected key: {obj_key!r}"

    assert storage.object_exists(obj_key), "object must exist in MinIO"

    print(f"\n[d] thumbnail_object_key = {obj_key!r}")
    print(f"[d] MinIO object exists: {storage.object_exists(obj_key)}")


# ===========================================================================
# (e) Notice absent from next cycle → marked withdrawn
# ===========================================================================

def test_e_withdrawn_notice(
    processor: NoticeProcessor,
    session_factory: Any,
) -> None:
    # Create a notice that will go missing in the next cycle.
    processor.handle_upsert(_payload("TEST/003"))

    # Fabricate a cycle that completed successfully but did NOT include TEST/003.
    cycle_start = datetime.now(tz=UTC).isoformat()
    time.sleep(0.05)

    # Give TEST/003 a last_seen_at that predates the cycle so list_stale finds it.
    with session_scope(session_factory) as s:
        n = s.get(Notice, "TEST/003")
        assert n is not None
        # Back-date last_seen_at so it falls before cycle_start.
        s.execute(
            text(
                "UPDATE notices SET last_seen_at = now() - interval '1 hour'"
                " WHERE notice_id = 'TEST/003'"
            )
        )

    manifest = {
        "cycle_id": "cycle-withdraw-test",
        "notice_ids": ["TEST/001", "TEST/002"],  # TEST/003 absent
        "total": 2,
        "errors": 0,
        "status": "ok",
        "started_at": cycle_start,
    }
    count = processor.handle_cycle_complete(manifest)
    assert count == 1, f"expected 1 withdrawal, got {count}"

    notice = _get_notice(session_factory, "TEST/003")
    assert notice is not None
    assert notice.status == NoticeStatus.withdrawn.value

    history = _get_history(session_factory, "TEST/003")
    withdrawn_rows = [h for h in history if h.change_type == ChangeType.withdrawn.value]
    assert len(withdrawn_rows) == 1

    print(f"\n[e] TEST/003 status = {notice.status!r}")
    print(f"[e] withdrawal history row: version={withdrawn_rows[0].version} "
          f"change_type={withdrawn_rows[0].change_type!r}")


# ===========================================================================
# Withdrawal guard: partial cycle must NOT withdraw
# ===========================================================================

def test_withdrawal_guard_partial_cycle_blocked(
    processor: NoticeProcessor,
    session_factory: Any,
) -> None:
    processor.handle_upsert(_payload("TEST/004"))

    count = processor.handle_cycle_complete({
        "cycle_id": "partial",
        "notice_ids": [],
        "total": 0,
        "errors": 5,
        "status": "partial",
        "started_at": datetime.now(tz=UTC).isoformat(),
    })
    assert count == 0

    notice = _get_notice(session_factory, "TEST/004")
    assert notice is not None
    assert notice.status == NoticeStatus.active.value, "guard must not withdraw on partial cycle"

    print(f"\n[guard] TEST/004 status = {notice.status!r} — correctly unchanged")


def test_withdrawal_guard_too_small_blocked(
    processor: NoticeProcessor,
    session_factory: Any,
) -> None:
    # _FakeSettings.WITHDRAWAL_MIN_CYCLE_SIZE = 1, so total=0 is blocked.
    count = processor.handle_cycle_complete({
        "cycle_id": "tiny",
        "notice_ids": [],
        "total": 0,
        "errors": 0,
        "status": "ok",
        "started_at": datetime.now(tz=UTC).isoformat(),
    })
    assert count == 0
    print("\n[guard] total=0 < min=1 — withdrawal correctly skipped")
