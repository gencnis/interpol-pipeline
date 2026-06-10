from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_session, get_storage
from app.api.schemas import HistoryEntry, NoticeDetail, NoticeListItem, PaginatedResponse
from app.common.models import Notice
from app.common.storage import StorageClient

router = APIRouter()


def _presigned(storage: StorageClient, key: str | None) -> str | None:
    if key is None:
        return None
    try:
        return storage.get_presigned_url(key)
    except Exception:
        return None


@router.get("/notices", response_model=PaginatedResponse[NoticeListItem])
async def list_notices(
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    name: str | None = Query(default=None),
    forename: str | None = Query(default=None),
    nationality: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Any:
    stmt = select(Notice)

    if name:
        stmt = stmt.where(Notice.name.ilike(f"%{name}%"))
    if forename:
        stmt = stmt.where(Notice.forename.ilike(f"%{forename}%"))
    if nationality:
        stmt = stmt.where(cast(Notice.nationalities, Text).contains(nationality))
    if status:
        stmt = stmt.where(Notice.status == status)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Notice.last_changed_at.desc())
    stmt = stmt.offset((page - 1) * size).limit(size)

    rows = (await session.execute(stmt)).scalars().all()

    items: list[NoticeListItem] = [
        NoticeListItem(
            notice_id=n.notice_id,
            forename=n.forename,
            name=n.name,
            nationalities=list(n.nationalities or []),
            status=n.status,
            last_changed_at=n.last_changed_at,
            thumbnail_url=_presigned(storage, n.thumbnail_object_key),
        )
        for n in rows
    ]

    return PaginatedResponse[NoticeListItem](items=items, total=total, page=page, size=size)


@router.get("/notices/{notice_id:path}", response_model=NoticeDetail)
async def get_notice(
    notice_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Any:
    stmt = (
        select(Notice)
        .where(Notice.notice_id == notice_id)
        .options(selectinload(Notice.history))
    )
    notice = (await session.execute(stmt)).scalars().first()

    if notice is None:
        raise HTTPException(status_code=404, detail="Notice not found")

    history: list[HistoryEntry] = [
        HistoryEntry(
            id=h.id,
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
        nationalities=list(notice.nationalities or []),
        arrest_warrant_countries=list(notice.arrest_warrant_countries or []),
        charge_text=notice.charge_text,
        status=notice.status,
        first_seen_at=notice.first_seen_at,
        last_seen_at=notice.last_seen_at,
        last_changed_at=notice.last_changed_at,
        thumbnail_url=_presigned(storage, notice.thumbnail_object_key),
        history=history,
    )


__all__ = ["router"]
