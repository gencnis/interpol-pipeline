from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.api.deps import DBSession, Storage
from app.api.routers.notices import _presign, _to_notice_out
from app.common.models import Notice, NoticeHistory


def make_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(tags=["ui"])

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(
        request: Request,
        session: DBSession,
        storage: Storage,
        name: str | None = Query(None),
        nationality: str | None = Query(None),
        status: str | None = Query(None),
    ) -> Any:
        notices = await _query_notices(session, storage, name, nationality, status, page_size=50)

        # Unique nationality codes for the filter dropdown.
        nat_rows = list((await session.execute(select(Notice.nationalities))).scalars())
        nat_set: set[str] = set()
        for row in nat_rows:
            if isinstance(row, list):
                for item in row:
                    if isinstance(item, str):
                        nat_set.add(item)
        nationalities = sorted(nat_set)

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "notices": notices,
                "nationalities": nationalities,
                "filters": {"name": name, "nationality": nationality, "status": status},
            },
        )

    @router.get("/partials/notices", response_class=HTMLResponse)
    async def notices_partial(
        request: Request,
        session: DBSession,
        storage: Storage,
        name: str | None = Query(None),
        nationality: str | None = Query(None),
        status: str | None = Query(None),
    ) -> Any:
        notices = await _query_notices(session, storage, name, nationality, status, page_size=50)
        return templates.TemplateResponse(
            request,
            "partials/notice_list.html",
            {"notices": notices},
        )

    @router.get("/notices/{notice_id:path}", response_class=HTMLResponse)
    async def notice_detail(
        notice_id: str,
        request: Request,
        session: DBSession,
        storage: Storage,
    ) -> Any:
        notice = await session.get(Notice, notice_id)
        if notice is None:
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

        history_q = (
            select(NoticeHistory)
            .where(NoticeHistory.notice_id == notice_id)
            .order_by(NoticeHistory.version)
        )
        history = list((await session.scalars(history_q)).all())
        thumbnail_url = _presign(notice.thumbnail_object_key, storage)

        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "notice": notice,
                "thumbnail_url": thumbnail_url,
                "history": history,
            },
        )

    return router


async def _query_notices(
    session: Any,
    storage: Any,
    name: str | None,
    nationality: str | None,
    status: str | None,
    page_size: int = 50,
) -> list[Any]:
    q = select(Notice)
    if name:
        q = q.where(Notice.name.ilike(f"%{name}%") | Notice.forename.ilike(f"%{name}%"))
    if nationality:
        q = q.where(Notice.nationalities.contains([nationality]))
    if status:
        q = q.where(Notice.status == status)
    q = q.order_by(Notice.last_changed_at.desc()).limit(page_size)
    raw = list((await session.scalars(q)).all())
    return [_to_notice_out(n, storage) for n in raw]
