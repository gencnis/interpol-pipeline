from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.common.logging import get_logger
from app.common.redis_client import RedisPublisher
from app.worker.normalizer import compute_diff, content_hash, normalize
from app.worker.photo_service import PhotoService
from app.worker.repository import NoticeRepository

log = get_logger(__name__)

_SessionFactory = Callable[[], AbstractContextManager[Session]]


class NoticeProcessor:
    """Orchestrates notice persistence and change detection.

    Handles two RabbitMQ message types:

    ``notice.upsert``
        Normalise → hash → DB lookup → created / updated / noop + photo upload.

    ``cycle.complete``
        Withdrawal reconciliation with a two-condition safety guard:
        (1) cycle status must be "ok" (zero errors during the sweep)
        (2) total notices in the cycle must be >= WITHDRAWAL_MIN_CYCLE_SIZE

        Both guards prevent a failed or partial cycle (e.g. Interpol 403 mid-
        sweep, or far fewer results than usual) from incorrectly marking still-
        active notices as withdrawn.
    """

    def __init__(
        self,
        get_session: _SessionFactory,
        photo_service: PhotoService,
        settings: Any,
        redis_pub: RedisPublisher | None = None,
    ) -> None:
        self._get_session = get_session
        self._photo = photo_service
        self._settings = settings
        self._redis_pub = redis_pub

    # ------------------------------------------------------------------
    # Public handlers
    # ------------------------------------------------------------------

    def handle_upsert(self, payload: dict[str, Any]) -> str:
        """Process one notice message.  Returns 'created' | 'updated' | 'noop'."""
        notice_id: str = payload.get("notice_id", "")
        thumbnail_url: str | None = payload.get("thumbnail_url")
        now = datetime.now(tz=UTC)

        normalized = normalize(payload)
        hash_ = content_hash(normalized)

        result: str
        diff_data: dict[str, Any] | None = None

        with self._get_session() as session:
            repo = NoticeRepository(session)
            existing = repo.get(notice_id)

            if existing is None:
                obj_key = self._photo.process(notice_id, thumbnail_url)
                repo.create(notice_id, normalized, hash_, obj_key, payload, now)
                log.info("worker.notice_created", notice_id=notice_id)
                result = "created"
            elif existing.content_hash == hash_:
                repo.touch(existing, now)
                log.debug("worker.notice_noop", notice_id=notice_id)
                result = "noop"
            else:
                # Hash differs → compute field-level diff and persist the update.
                old_normalized = normalize(existing.raw_json)
                diff_data = compute_diff(old_normalized, normalized)
                obj_key = self._photo.process(notice_id, thumbnail_url)
                repo.update_state(existing, normalized, hash_, diff_data, obj_key, payload, now)
                log.info("worker.notice_updated", notice_id=notice_id, diff_keys=sorted(diff_data))
                result = "updated"

        # Publish Redis event AFTER the DB session closes (commit success).
        if self._redis_pub is not None:
            if result == "created":
                self._redis_pub.publish(
                    {"event": "created", "notice_id": notice_id,
                     "change_type": "created", "diff": None}
                )
            elif result == "updated":
                self._redis_pub.publish(
                    {"event": "updated", "notice_id": notice_id,
                     "change_type": "updated", "diff": diff_data}
                )

        return result

    def handle_cycle_complete(self, manifest: dict[str, Any]) -> int:
        """Run withdrawal reconciliation.  Returns count withdrawn (0 if guard blocks)."""
        cycle_id = manifest.get("cycle_id", "?")
        total = manifest.get("total", 0)
        errors = manifest.get("errors", 0)
        status = manifest.get("status", "unknown")
        started_at_iso: str = manifest.get("started_at", "")
        seen_ids: set[str] = set(manifest.get("notice_ids", []))

        # Guard 1: cycle must have completed without errors.
        if status != "ok":
            log.warning(
                "worker.withdrawal_skipped",
                reason="cycle_not_ok",
                status=status,
                errors=errors,
                cycle_id=cycle_id,
            )
            return 0

        # Guard 2: implausibly small cycle — likely a partial sweep.
        min_size: int = getattr(self._settings, "WITHDRAWAL_MIN_CYCLE_SIZE", 50)
        if total < min_size:
            log.warning(
                "worker.withdrawal_skipped",
                reason="cycle_too_small",
                total=total,
                min_required=min_size,
                cycle_id=cycle_id,
            )
            return 0

        try:
            cycle_start = datetime.fromisoformat(started_at_iso)
        except (ValueError, TypeError):
            cycle_start = datetime.now(tz=UTC)

        now = datetime.now(tz=UTC)
        withdrawn_ids: list[str] = []
        with self._get_session() as session:
            repo = NoticeRepository(session)
            stale = repo.list_stale(cycle_start)
            # Double-check: exclude any notice that appears in the manifest
            # (guards against clock skew between fetcher and worker).
            to_withdraw = [n for n in stale if n.notice_id not in seen_ids]
            withdrawn_ids = [n.notice_id for n in to_withdraw]
            count = repo.mark_withdrawn(to_withdraw, now)

        if count:
            log.info("worker.withdrawn", count=count, cycle_id=cycle_id)

        # Publish Redis withdrawal events AFTER the DB session closes.
        if self._redis_pub is not None:
            for wid in withdrawn_ids:
                self._redis_pub.publish(
                    {"event": "withdrawn", "notice_id": wid,
                     "change_type": "withdrawn", "diff": None}
                )

        return count
