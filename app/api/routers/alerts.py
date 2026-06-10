from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DBSession
from app.api.schemas import AlertOut, PagedOut
from app.common.models import Notice, NoticeHistory

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=PagedOut)
async def list_alerts(
    session: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> Any:
    base_q = (
        select(NoticeHistory)
        .where(NoticeHistory.change_type.in_(["updated", "withdrawn"]))
    )

    total: int = (
        await session.execute(select(func.count()).select_from(base_q.subquery()))
    ).scalar_one()

    rows_q = (
        select(
            NoticeHistory.id,
            NoticeHistory.notice_id,
            NoticeHistory.version,
            NoticeHistory.change_type,
            NoticeHistory.diff,
            NoticeHistory.recorded_at,
            Notice.forename,
            Notice.name,
        )
        .join(Notice, NoticeHistory.notice_id == Notice.notice_id)
        .where(NoticeHistory.change_type.in_(["updated", "withdrawn"]))
        .order_by(NoticeHistory.recorded_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(rows_q)).all()

    items = [
        AlertOut(
            id=r.id,
            notice_id=r.notice_id,
            version=r.version,
            change_type=r.change_type,
            diff=r.diff,
            recorded_at=r.recorded_at,
            forename=r.forename,
            name=r.name,
        )
        for r in rows
    ]
    pages = max(1, -(-total // page_size))
    return PagedOut(items=items, total=total, page=page, page_size=page_size, pages=pages)
