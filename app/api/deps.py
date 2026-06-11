from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine: Any = None
_factory: Any = None


def _get_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _factory
    if _factory is None:
        from app.common.config import get_settings

        settings = get_settings()
        _engine = create_async_engine(settings.POSTGRES_DSN, pool_pre_ping=True)
        _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _get_factory()() as session:
        yield session


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
