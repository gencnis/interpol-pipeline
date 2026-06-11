from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.api.schemas import AlertItem, PagedResponse
from app.common.models import Notice, NoticeHistory

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

_ALERT_CHANGE_TYPES = ("updated", "withdrawn")


@router.get("", response_model=PagedResponse[AlertItem])
async def list_alerts(
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
) -> PagedResponse[AlertItem]:
    base_stmt = (
        select(NoticeHistory, Notice.forename, Notice.name)
        .join(Notice, NoticeHistory.notice_id == Notice.notice_id)
        .where(NoticeHistory.change_type.in_(_ALERT_CHANGE_TYPES))
    )

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        base_stmt.order_by(NoticeHistory.recorded_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()

    items: list[AlertItem] = []
    for row in rows:
        h: NoticeHistory = row[0]
        forename: str | None = row[1]
        name: str | None = row[2]
        parts = [p for p in (forename, name) if p]
        notice_name = " ".join(parts) if parts else None
        items.append(
            AlertItem(
                id=h.id,
                notice_id=h.notice_id,
                notice_name=notice_name,
                version=h.version,
                change_type=h.change_type,
                diff=h.diff,
                recorded_at=h.recorded_at,
            )
        )

    return PagedResponse(items=items, total=total, page=page, page_size=page_size)
