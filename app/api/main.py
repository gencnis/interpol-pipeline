from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket
from fastapi.templating import Jinja2Templates

from app.api.routers import alerts, notices
from app.api.routers import ui as ui_router
from app.api.ws import ws_alerts
from app.common.config import Settings, get_settings
from app.common.db import get_async_engine, make_async_session_factory
from app.common.logging import configure_logging, get_logger
from app.common.storage import StorageClient

_TEMPLATE_DIR = Path(__file__).parent.parent / "web" / "templates"


def create_app(settings: Settings | None = None) -> FastAPI:
    _settings = settings or get_settings()

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        configure_logging(_settings)
        log = get_logger(__name__)
        log.info("api.starting")

        engine = get_async_engine(_settings)
        app.state.settings = _settings
        app.state.session_factory = make_async_session_factory(engine)
        app.state.storage = StorageClient(_settings)
        app.state.redis = aioredis.from_url(_settings.REDIS_URL)

        yield

        await engine.dispose()
        await app.state.redis.aclose()
        log.info("api.stopped")

    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    _app = FastAPI(title="Interpol Pipeline API", lifespan=_lifespan)
    _app.include_router(notices.router)
    _app.include_router(alerts.router)
    _app.include_router(ui_router.make_router(templates))

    @_app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @_app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ok"}

    @_app.websocket("/ws/alerts")
    async def _ws_alerts(websocket: WebSocket) -> None:
        await ws_alerts(websocket)

    return _app


# Module-level instance used by uvicorn.
app = create_app()
