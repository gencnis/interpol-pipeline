from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AppSettings, DBSession, Storage
from app.api.schemas import HistoryOut, NoticeDetailOut, NoticeOut, PagedOut
from app.common.models import Notice, NoticeHistory
from app.common.storage import StorageClient

router = APIRouter(prefix="/api/notices", tags=["notices"])


def _presign(key: str | None, storage: StorageClient) -> str | None:
    if not key:
        return None
    try:
        return storage.get_presigned_url(key)
    except Exception:
        return None


def _to_notice_out(notice: Notice, storage: StorageClient) -> NoticeOut:
    out = NoticeOut.model_validate(notice)
    out.thumbnail_url = _presign(notice.thumbnail_object_key, storage)
    return out


@router.get("", response_model=PagedOut)
async def list_notices(
    session: DBSession,
    settings: AppSettings,
    storage: Storage,
    name: str | None = Query(None),
    nationality: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> Any:
    q = select(Notice)
    if name:
        q = q.where(Notice.name.ilike(f"%{name}%") | Notice.forename.ilike(f"%{name}%"))
    if nationality:
        # Check if the JSONB array contains the given nationality code.
        q = q.where(Notice.nationalities.contains([nationality]))
    if status:
        q = q.where(Notice.status == status)

    total_q = select(func.count()).select_from(q.subquery())
    total: int = (await session.execute(total_q)).scalar_one()

    q = q.order_by(Notice.last_changed_at.desc()).offset((page - 1) * page_size).limit(page_size)
    notices = list((await session.scalars(q)).all())

    items = [_to_notice_out(n, storage) for n in notices]
    pages = max(1, -(-total // page_size))
    return PagedOut(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/{notice_id:path}", response_model=NoticeDetailOut)
async def get_notice(
    notice_id: str,
    session: DBSession,
    settings: AppSettings,
    storage: Storage,
) -> Any:
    notice = await session.get(Notice, notice_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="Notice not found")

    history_q = (
        select(NoticeHistory)
        .where(NoticeHistory.notice_id == notice_id)
        .order_by(NoticeHistory.version)
    )
    history = list((await session.scalars(history_q)).all())

    # Use NoticeOut.model_validate (no history field) to avoid triggering the
    # SQLAlchemy lazy-load of the history relationship in async context.
    base = NoticeOut.model_validate(notice)
    data = {
        **base.model_dump(),
        "thumbnail_url": _presign(notice.thumbnail_object_key, storage),
        "history": [HistoryOut.model_validate(h) for h in history],
    }
    return NoticeDetailOut.model_construct(**data)
