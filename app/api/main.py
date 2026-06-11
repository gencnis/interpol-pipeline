from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routers import alerts, notices, ui, ws
from app.common.config import get_settings
from app.common.logging import configure_logging, get_logger


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging(get_settings())
    get_logger(__name__).info("api.starting")
    yield
    from app.api.deps import dispose_engine

    await dispose_engine()
    get_logger(__name__).info("api.stopped")


app = FastAPI(title="Interpol Pipeline API", lifespan=_lifespan)

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

app.include_router(ui.router)
app.include_router(notices.router)
app.include_router(alerts.router)
app.include_router(ws.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
