from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.common.logging import get_logger
from app.worker.normalizer import compute_diff, content_hash, normalize
from app.worker.photo_service import PhotoService
from app.worker.redis_publisher import RedisPublisher
from app.worker.repository import NoticeRepository

log = get_logger(__name__)

_SessionFactory = Callable[[], AbstractContextManager[Session]]


def _notice_name(normalized: dict[str, Any]) -> str | None:
    parts = [
        p
        for p in (
            str(normalized.get("forename") or "").strip(),
            str(normalized.get("name") or "").strip(),
        )
        if p
    ]
    return " ".join(parts) or None


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
        redis_publisher: RedisPublisher | None = None,
    ) -> None:
        self._get_session = get_session
        self._photo = photo_service
        self._settings = settings
        self._redis_publisher = redis_publisher

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

        with self._get_session() as session:
            repo = NoticeRepository(session)
            existing = repo.get(notice_id)

            if existing is None:
                obj_key = self._photo.process(notice_id, thumbnail_url)
                repo.create(notice_id, normalized, hash_, obj_key, payload, now)
                log.info("worker.notice_created", notice_id=notice_id)
                if self._redis_publisher:
                    _name = _notice_name(normalized)
                    self._redis_publisher.publish(
                        "created", notice_id, _name, None, now.isoformat()
                    )
                return "created"

            if existing.content_hash == hash_:
                repo.touch(existing, now)
                log.debug("worker.notice_noop", notice_id=notice_id)
                return "noop"

            # Hash differs → compute field-level diff and persist the update.
            old_normalized = normalize(existing.raw_json)
            diff = compute_diff(old_normalized, normalized)
            obj_key = self._photo.process(notice_id, thumbnail_url)
            repo.update_state(existing, normalized, hash_, diff, obj_key, payload, now)
            log.info("worker.notice_updated", notice_id=notice_id, diff_keys=sorted(diff))
            if self._redis_publisher:
                _name = _notice_name(normalized)
                self._redis_publisher.publish("updated", notice_id, _name, diff, now.isoformat())
            return "updated"

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
        with self._get_session() as session:
            repo = NoticeRepository(session)
            stale = repo.list_stale(cycle_start)
            # Double-check: exclude any notice that appears in the manifest
            # (guards against clock skew between fetcher and worker).
            to_withdraw = [n for n in stale if n.notice_id not in seen_ids]
            count = repo.mark_withdrawn(to_withdraw, now)

        if count:
            log.info("worker.withdrawn", count=count, cycle_id=cycle_id)
            if self._redis_publisher:
                for n in to_withdraw:
                    withdrawn_normalized = normalize(n.raw_json)
                    _name = _notice_name(withdrawn_normalized)
                    self._redis_publisher.publish(
                        "withdrawn", n.notice_id, _name, None, now.isoformat()
                    )
        return count
