from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.common.config import get_settings
from app.common.logging import configure_logging, get_logger


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging(get_settings())
    get_logger(__name__).info("api.starting")
    yield
    get_logger(__name__).info("api.stopped")


app = FastAPI(title="Interpol Pipeline API", lifespan=_lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
