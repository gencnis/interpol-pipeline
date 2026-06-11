from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_db, get_storage
from app.api.schemas import AlertItem, HistoryItem, NoticeDetail, NoticeItem
from app.common.models import Notice, NoticeHistory
from app.common.storage import StorageClient

router = APIRouter(tags=["ui"])

_TEMPLATES_DIR = os.path.join(
    os.environ.get("APP_WEB_PATH", str(Path(__file__).parent.parent.parent / "web")),
    "templates",
)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_ALERT_CHANGE_TYPES = ("updated", "withdrawn")


def _presigned(storage: StorageClient, key: str | None) -> str | None:
    if key is None:
        return None
    try:
        return storage.get_presigned_url(key)
    except Exception:
        return None


@router.get("/")
async def index(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> object:
    # Notices (active first, latest-changed first)
    stmt = (
        select(Notice)
        .order_by(Notice.last_changed_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    notice_rows = (await db.execute(stmt)).scalars().all()
    total_stmt = select(func.count()).select_from(select(Notice).subquery())
    total: int = (await db.execute(total_stmt)).scalar_one()

    notices = [
        NoticeItem(
            notice_id=n.notice_id,
            forename=n.forename,
            name=n.name,
            status=n.status,
            nationalities=n.nationalities or [],
            thumbnail_url=_presigned(storage, n.thumbnail_object_key),
            last_changed_at=n.last_changed_at,
        )
        for n in notice_rows
    ]

    # Recent alerts
    alert_stmt = (
        select(NoticeHistory, Notice.forename, Notice.name)
        .join(Notice, NoticeHistory.notice_id == Notice.notice_id)
        .where(NoticeHistory.change_type.in_(_ALERT_CHANGE_TYPES))
        .order_by(NoticeHistory.recorded_at.desc())
        .limit(20)
    )
    alert_rows = (await db.execute(alert_stmt)).all()
    alerts: list[AlertItem] = []
    for row in alert_rows:
        h: NoticeHistory = row[0]
        forename: str | None = row[1]
        name: str | None = row[2]
        parts = [p for p in (forename, name) if p]
        notice_name = " ".join(parts) if parts else None
        alerts.append(
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

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "notices": notices,
            "alerts": alerts,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/notices")
async def notices_partial(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    name: str = "",
    nationality: str = "",
    status: str = "",
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> object:
    stmt = select(Notice)

    if name:
        from sqlalchemy import or_

        pattern = f"%{name}%"
        stmt = stmt.where(
            or_(Notice.name.ilike(pattern), Notice.forename.ilike(pattern))
        )
    if nationality:
        stmt = stmt.where(Notice.nationalities.contains([nationality]))
    if status:
        stmt = stmt.where(Notice.status == status)

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(total_stmt)).scalar_one()

    stmt = (
        stmt.order_by(Notice.last_changed_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    notices = [
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

    # HTMX partial vs full page
    hx_request = request.headers.get("HX-Request")
    if hx_request:
        return templates.TemplateResponse(
            request=request,
            name="_notice_list.html",
            context={
                "notices": notices,
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "notices": notices,
            "alerts": [],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/notices/{notice_id:path}")
async def notice_detail(
    notice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> object:
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

    detail = NoticeDetail(
        notice_id=notice.notice_id,
        forename=notice.forename,
        name=notice.name,
        sex_id=notice.sex_id,
        date_of_birth=notice.date_of_birth,
        nationalities=notice.nationalities,
        arrest_warrant_countries=notice.arrest_warrant_countries,
        charge_text=notice.charge_text,
        status=notice.status,
        thumbnail_url=_presigned(storage, notice.thumbnail_object_key),
        first_seen_at=notice.first_seen_at,
        last_seen_at=notice.last_seen_at,
        last_changed_at=notice.last_changed_at,
        history=history,
    )

    return templates.TemplateResponse(
        request=request,
        name="notice_detail.html",
        context={"notice": detail.model_dump()},
    )
