from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.common.logging import get_logger
from app.common.models import ChangeType, Notice, NoticeHistory, NoticeStatus

log = get_logger(__name__)


class NoticeRepository:
    """All DB operations for the persistence + change-detection path.

    Every method operates within the session passed at construction time;
    commit/rollback is the caller's responsibility (see db.session_scope).
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, notice_id: str) -> Notice | None:
        return self._s.get(Notice, notice_id)

    def next_version(self, notice_id: str) -> int:
        result = self._s.execute(
            select(func.coalesce(func.max(NoticeHistory.version), 0)).where(
                NoticeHistory.notice_id == notice_id
            )
        ).scalar()
        return (result or 0) + 1

    def list_stale(self, before: datetime) -> list[Notice]:
        """Active notices whose last_seen_at predates the cycle start."""
        return list(
            self._s.scalars(
                select(Notice).where(
                    Notice.status == NoticeStatus.active.value,
                    Notice.last_seen_at < before,
                )
            )
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(
        self,
        notice_id: str,
        normalized: dict[str, Any],
        hash_: str,
        thumbnail_object_key: str | None,
        raw_json: dict[str, Any],
        now: datetime,
    ) -> Notice:
        notice = Notice(
            notice_id=notice_id,
            forename=normalized.get("forename"),
            name=normalized.get("name"),
            sex_id=normalized.get("sex_id"),
            date_of_birth=normalized.get("date_of_birth"),
            nationalities=normalized.get("nationalities", []),
            arrest_warrant_countries=normalized.get("arrest_warrant_countries", []),
            charge_text=normalized.get("charge_text"),
            thumbnail_object_key=thumbnail_object_key,
            content_hash=hash_,
            status=NoticeStatus.active.value,
            raw_json=raw_json,
            first_seen_at=now,
            last_seen_at=now,
            last_changed_at=now,
        )
        self._s.add(notice)
        self._s.add(
            NoticeHistory(
                notice_id=notice_id,
                version=1,
                change_type=ChangeType.created.value,
                content_hash=hash_,
                snapshot=normalized,
                diff=None,
                valid_from=now,
                valid_to=None,
                recorded_at=now,
            )
        )
        return notice

    def update_state(
        self,
        notice: Notice,
        normalized: dict[str, Any],
        hash_: str,
        diff: dict[str, Any],
        thumbnail_object_key: str | None,
        raw_json: dict[str, Any],
        now: datetime,
    ) -> None:
        # Close the still-open history row for the previous version.
        self._s.execute(
            update(NoticeHistory)
            .where(
                NoticeHistory.notice_id == notice.notice_id,
                NoticeHistory.valid_to.is_(None),
            )
            .values(valid_to=now)
        )
        version = self.next_version(notice.notice_id)

        notice.forename = normalized.get("forename")
        notice.name = normalized.get("name")
        notice.sex_id = normalized.get("sex_id")
        notice.date_of_birth = normalized.get("date_of_birth")
        notice.nationalities = normalized.get("nationalities", [])
        notice.arrest_warrant_countries = normalized.get("arrest_warrant_countries", [])
        notice.charge_text = normalized.get("charge_text")
        notice.content_hash = hash_
        notice.status = NoticeStatus.active.value
        notice.raw_json = raw_json
        notice.last_seen_at = now
        notice.last_changed_at = now
        if thumbnail_object_key is not None:
            notice.thumbnail_object_key = thumbnail_object_key

        self._s.add(
            NoticeHistory(
                notice_id=notice.notice_id,
                version=version,
                change_type=ChangeType.updated.value,
                content_hash=hash_,
                snapshot=normalized,
                diff=diff,
                valid_from=now,
                valid_to=None,
                recorded_at=now,
            )
        )

    def touch(self, notice: Notice, now: datetime) -> None:
        """Idempotent no-op: advance last_seen_at only, no history row."""
        notice.last_seen_at = now
        notice.status = NoticeStatus.active.value

    def mark_withdrawn(self, notices: list[Notice], now: datetime) -> int:
        for notice in notices:
            self._s.execute(
                update(NoticeHistory)
                .where(
                    NoticeHistory.notice_id == notice.notice_id,
                    NoticeHistory.valid_to.is_(None),
                )
                .values(valid_to=now)
            )
            version = self.next_version(notice.notice_id)
            notice.status = NoticeStatus.withdrawn.value
            notice.last_changed_at = now
            self._s.add(
                NoticeHistory(
                    notice_id=notice.notice_id,
                    version=version,
                    change_type=ChangeType.withdrawn.value,
                    content_hash=notice.content_hash,
                    snapshot={},
                    diff=None,
                    valid_from=now,
                    valid_to=None,
                    recorded_at=now,
                )
            )
        return len(notices)
