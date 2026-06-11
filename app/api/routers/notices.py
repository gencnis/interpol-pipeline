from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_db, get_storage
from app.api.schemas import HistoryItem, NoticeDetail, NoticeItem, PagedResponse
from app.common.models import Notice
from app.common.storage import StorageClient

router = APIRouter(prefix="/api/notices", tags=["notices"])


def _presigned(storage: StorageClient, key: str | None) -> str | None:
    if key is None:
        return None
    try:
        return storage.get_presigned_url(key)
    except Exception:
        return None


@router.get("", response_model=PagedResponse[NoticeItem])
async def list_notices(
    page: int = 1,
    page_size: int = 50,
    name: str = "",
    nationality: str = "",
    status: str = "",
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> PagedResponse[NoticeItem]:
    stmt = select(Notice)

    if name:
        pattern = f"%{name}%"
        from sqlalchemy import or_

        stmt = stmt.where(
            or_(
                Notice.name.ilike(pattern),
                Notice.forename.ilike(pattern),
            )
        )
    if nationality:
        stmt = stmt.where(Notice.nationalities.contains([nationality]))
    if status:
        stmt = stmt.where(Notice.status == status)

    # Total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    # Paginated rows
    stmt = stmt.order_by(Notice.last_changed_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = [
        NoticeItem(
            notice_id=n.notice_id,
            forename=n.forename,
            name=n.name,
            status=n.status,
            nationalities=n.nationalities or [],
            thumbnail_url=_presigned(storage, n.thumbnail_object_key),
            last_changed_at=n.last_changed_at,
        )
        for n in rows
    ]
    return PagedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{notice_id:path}", response_model=NoticeDetail)
async def get_notice(
    notice_id: str,
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> NoticeDetail:
    stmt = (
        select(Notice)
        .where(Notice.notice_id == notice_id)
        .options(selectinload(Notice.history))
    )
    result = await db.execute(stmt)
    notice = result.scalar_one_or_none()
    if notice is None:
        raise HTTPException(status_code=404, detail="Notice not found")

    history = [
        HistoryItem(
            version=h.version,
            change_type=h.change_type,
            diff=h.diff,
            recorded_at=h.recorded_at,
        )
        for h in sorted(notice.history, key=lambda h: h.version)
    ]

    return NoticeDetail(
        notice_id=notice.notice_id,
        forename=notice.forename,
        name=notice.name,
        sex_id=notice.sex_id,
        date_of_birth=notice.date_of_birth,
        nationalities=notice.nationalities or [],
        arrest_warrant_countries=notice.arrest_warrant_countries or [],
        charge_text=notice.charge_text,
        status=notice.status,
        thumbnail_url=_presigned(storage, notice.thumbnail_object_key),
        first_seen_at=notice.first_seen_at,
        last_seen_at=notice.last_seen_at,
        last_changed_at=notice.last_changed_at,
        history=history,
    )
