from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import deps
from app.api.routers import alerts, notices, ui
from app.api.routers import ws as ws_router
from app.api.routers.ws import broadcast_redis_events
from app.common.config import get_settings
from app.common.logging import configure_logging, get_logger


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    log.info("api.starting")
    deps.init_async_db(settings)
    deps.init_storage(settings)
    task = asyncio.create_task(
        broadcast_redis_events(settings.REDIS_URL, settings.REDIS_EVENT_CHANNEL)
    )
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await deps.close_async_db()
    log.info("api.stopped")


app = FastAPI(title="Interpol Pipeline API", lifespan=_lifespan)
app.include_router(notices.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(ws_router.router)
app.include_router(ui.router)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent.parent / "web" / "static")),
    name="static",
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
