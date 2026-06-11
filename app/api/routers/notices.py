from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.common.config import get_settings
from app.common.models import Notice, NoticeHistory
from app.common.storage import StorageClient

router = APIRouter(prefix="/api/notices", tags=["notices"])


def _notice_dict(n: Notice) -> dict[str, Any]:
    return {
        "notice_id": n.notice_id,
        "forename": n.forename,
        "name": n.name,
        "sex_id": n.sex_id,
        "date_of_birth": n.date_of_birth,
        "nationalities": n.nationalities,
        "arrest_warrant_countries": n.arrest_warrant_countries,
        "charge_text": n.charge_text,
        "thumbnail_object_key": n.thumbnail_object_key,
        "status": n.status,
        "first_seen_at": n.first_seen_at.isoformat() if n.first_seen_at else None,
        "last_seen_at": n.last_seen_at.isoformat() if n.last_seen_at else None,
        "last_changed_at": n.last_changed_at.isoformat() if n.last_changed_at else None,
    }


def _history_dict(h: NoticeHistory) -> dict[str, Any]:
    return {
        "id": h.id,
        "version": h.version,
        "change_type": h.change_type,
        "content_hash": h.content_hash,
        "diff": h.diff,
        "valid_from": h.valid_from.isoformat() if h.valid_from else None,
        "valid_to": h.valid_to.isoformat() if h.valid_to else None,
        "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None,
    }


@router.get("")
async def list_notices(
    name: str | None = Query(None),
    nationality: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    base_q = select(Notice)
    if name:
        base_q = base_q.where(Notice.name.ilike(f"%{name}%"))
    if nationality:
        base_q = base_q.where(Notice.nationalities.contains([nationality]))
    if status:
        base_q = base_q.where(Notice.status == status)

    total = (
        await db.execute(select(func.count()).select_from(base_q.subquery()))
    ).scalar_one()

    items_q = (
        base_q.order_by(Notice.last_changed_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    notices = list((await db.execute(items_q)).scalars())
    return {
        "items": [_notice_dict(n) for n in notices],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{notice_id:path}")
async def get_notice(
    notice_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    notice = await db.get(Notice, notice_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="Notice not found")

    settings = get_settings()
    photo_url: str | None = None
    if notice.thumbnail_object_key:
        storage = StorageClient(settings)
        photo_url = storage.get_presigned_url(notice.thumbnail_object_key)

    history_rows = list(
        (
            await db.execute(
                select(NoticeHistory)
                .where(NoticeHistory.notice_id == notice_id)
                .order_by(NoticeHistory.version)
            )
        ).scalars()
    )

    result = _notice_dict(notice)
    result["photo_url"] = photo_url
    result["history"] = [_history_dict(h) for h in history_rows]
    return result
