from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.routers.notices import _history_dict, _notice_dict
from app.common.config import get_settings
from app.common.models import Notice, NoticeHistory
from app.common.storage import StorageClient

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    name: str | None = Query(None),
    nationality: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> Any:
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

    paginated = (
        base_q.order_by(Notice.last_changed_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    notices = list((await db.execute(paginated)).scalars())

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "notices": [_notice_dict(n) for n in notices],
            "total": total,
            "page": page,
            "per_page": per_page,
            "filter_name": name,
            "filter_nationality": nationality,
            "filter_status": status,
        },
    )


@router.get("/notices/{notice_id:path}", response_class=HTMLResponse)
async def notice_detail(
    request: Request,
    notice_id: str,
    db: AsyncSession = Depends(get_db),
) -> Any:
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

    nd = _notice_dict(notice)
    nd["photo_url"] = photo_url

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "notice": nd,
            "history": [_history_dict(h) for h in history_rows],
        },
    )
