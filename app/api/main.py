from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.db import get_async_engine, get_async_session_factory
from app.api.dependencies import setup_db, setup_storage
from app.api.routers import alerts, notices, ui
from app.api.websocket import router as ws_router
from app.common.config import get_settings
from app.common.db import run_migrations
from app.common.logging import configure_logging, get_logger
from app.common.storage import StorageClient

_WEB_PATH = os.environ.get(
    "APP_WEB_PATH",
    str(Path(__file__).parent.parent / "web"),
)
_STATIC_PATH = os.path.join(_WEB_PATH, "static")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    log.info("api.starting")

    # Run migrations (sync — uses POSTGRES_SYNC_DSN)
    run_migrations(settings)

    # Async DB
    engine = get_async_engine(settings)
    factory = get_async_session_factory(engine)
    setup_db(factory)

    # Storage (presigned URLs only — bucket creation is the worker's job)
    storage = StorageClient(settings)
    setup_storage(storage)

    log.info("api.ready")
    yield

    await engine.dispose()
    log.info("api.stopped")


app = FastAPI(title="Interpol Pipeline API", lifespan=_lifespan)

# Static files (only mount if path exists — avoids crash when templates not yet present)
if os.path.isdir(_STATIC_PATH):
    app.mount("/static", StaticFiles(directory=_STATIC_PATH), name="static")

# Routers
app.include_router(notices.router)
app.include_router(alerts.router)
app.include_router(ui.router)
app.include_router(ws_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
