from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.common.models import ChangeType, Notice, NoticeHistory

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

_ALERT_TYPES = [ChangeType.updated.value, ChangeType.withdrawn.value]


@router.get("")
async def list_alerts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    base_q = select(NoticeHistory).where(NoticeHistory.change_type.in_(_ALERT_TYPES))

    total = (
        await db.execute(select(func.count()).select_from(base_q.subquery()))
    ).scalar_one()

    items_q = (
        base_q.options(selectinload(NoticeHistory.notice))
        .order_by(NoticeHistory.recorded_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = list((await db.execute(items_q)).scalars())

    items = []
    for h in rows:
        n: Notice | None = h.notice  # type: ignore[assignment]
        items.append(
            {
                "id": h.id,
                "notice_id": h.notice_id,
                "version": h.version,
                "change_type": h.change_type,
                "content_hash": h.content_hash,
                "diff": h.diff,
                "valid_from": h.valid_from.isoformat() if h.valid_from else None,
                "valid_to": h.valid_to.isoformat() if h.valid_to else None,
                "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None,
                "notice_forename": n.forename if n else None,
                "notice_name": n.name if n else None,
            }
        )
    return {"items": items, "total": total, "page": page, "per_page": per_page}
