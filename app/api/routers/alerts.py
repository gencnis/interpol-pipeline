from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.schemas import AlertItem, PaginatedResponse
from app.common.models import ChangeType, NoticeHistory

router = APIRouter()

_ALERT_CHANGE_TYPES = [ChangeType.updated.value, ChangeType.withdrawn.value]


@router.get("/alerts", response_model=PaginatedResponse[AlertItem])
async def list_alerts(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Any:
    base_stmt = select(NoticeHistory).where(
        NoticeHistory.change_type.in_(_ALERT_CHANGE_TYPES)
    )

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        base_stmt.order_by(NoticeHistory.recorded_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await session.execute(stmt)).scalars().all()

    items: list[AlertItem] = [
        AlertItem(
            id=h.id,
            notice_id=h.notice_id,
            change_type=h.change_type,
            diff=h.diff,
            recorded_at=h.recorded_at,
        )
        for h in rows
    ]

    return PaginatedResponse[AlertItem](items=items, total=total, page=page, size=size)


__all__ = ["router"]
