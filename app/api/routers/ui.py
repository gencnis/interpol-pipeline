from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_session, get_settings, get_storage
from app.api.schemas import HistoryEntry, NoticeDetail, NoticeListItem
from app.common.config import Settings
from app.common.models import Notice
from app.common.storage import StorageClient

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


async def _fetch_notice_list(
    session: AsyncSession,
    storage: StorageClient,
    name: str | None,
    forename: str | None,
    nationality: str | None,
    status: str | None,
    page: int,
    size: int,
) -> tuple[list[NoticeListItem], int]:
    stmt = select(Notice)
    if name:
        stmt = stmt.where(Notice.name.ilike(f"%{name}%"))
    if forename:
        stmt = stmt.where(Notice.forename.ilike(f"%{forename}%"))
    if nationality:
        stmt = stmt.where(Notice.nationalities.cast(str).contains(nationality))
    if status:
        stmt = stmt.where(Notice.status == status)
    stmt = stmt.order_by(Notice.last_changed_at.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    stmt = stmt.offset((page - 1) * size).limit(size)
    rows = (await session.execute(stmt)).scalars().all()

    items: list[NoticeListItem] = []
    for n in rows:
        thumb_url: str | None = None
        if n.thumbnail_object_key:
            try:
                thumb_url = storage.get_presigned_url(n.thumbnail_object_key)
            except Exception:
                pass
        items.append(
            NoticeListItem(
                notice_id=n.notice_id,
                forename=n.forename,
                name=n.name,
                nationalities=list(n.nationalities or []),
                status=str(n.status),
                last_changed_at=n.last_changed_at,
                thumbnail_url=thumb_url,
            )
        )
    return items, total


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    name: str | None = None,
    forename: str | None = None,
    nationality: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: StorageClient = Depends(get_storage),
) -> Any:
    notices, total = await _fetch_notice_list(
        session, storage, name, forename, nationality, status, page, size
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "notices": notices,
            "total": total,
            "page": page,
            "size": size,
        },
    )


@router.get("/partials/notices", response_class=HTMLResponse)
async def notices_partial(
    request: Request,
    name: str | None = None,
    forename: str | None = None,
    nationality: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: StorageClient = Depends(get_storage),
) -> Any:
    notices, total = await _fetch_notice_list(
        session, storage, name, forename, nationality, status, page, size
    )
    return templates.TemplateResponse(
        request,
        "partials/notice_list.html",
        {
            "notices": notices,
            "total": total,
            "page": page,
            "size": size,
        },
    )


@router.get("/notices/{notice_id:path}", response_class=HTMLResponse)
async def notice_detail(
    request: Request,
    notice_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: StorageClient = Depends(get_storage),
) -> Any:
    stmt = (
        select(Notice)
        .where(Notice.notice_id == notice_id)
        .options(selectinload(Notice.history))
    )
    notice = (await session.execute(stmt)).scalars().first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    thumb_url: str | None = None
    if notice.thumbnail_object_key:
        try:
            thumb_url = storage.get_presigned_url(notice.thumbnail_object_key)
        except Exception:
            pass

    history_items = [
        HistoryEntry(
            id=h.id,
            version=h.version,
            change_type=str(h.change_type),
            diff=h.diff,
            recorded_at=h.recorded_at,
        )
        for h in sorted(notice.history, key=lambda x: x.version)
    ]

    detail = NoticeDetail(
        notice_id=notice.notice_id,
        forename=notice.forename,
        name=notice.name,
        sex_id=notice.sex_id,
        date_of_birth=notice.date_of_birth,
        nationalities=list(notice.nationalities or []),
        arrest_warrant_countries=list(notice.arrest_warrant_countries or []),
        charge_text=notice.charge_text,
        status=str(notice.status),
        first_seen_at=notice.first_seen_at,
        last_seen_at=notice.last_seen_at,
        last_changed_at=notice.last_changed_at,
        thumbnail_url=thumb_url,
        history=history_items,
    )

    return templates.TemplateResponse(
        request,
        "detail.html",
        {"notice": detail},
    )
