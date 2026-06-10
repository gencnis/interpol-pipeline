from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.common.config import Settings
from app.common.config import get_settings as _get_settings
from app.common.storage import StorageClient

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_storage: StorageClient | None = None


def init_async_db(settings: Settings) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(settings.POSTGRES_DSN, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def init_storage(settings: Settings) -> None:
    global _storage
    _storage = StorageClient(settings)


async def close_async_db() -> None:
    if _engine:
        await _engine.dispose()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


def get_settings() -> Settings:
    return _get_settings()


def get_storage() -> StorageClient:
    assert _storage is not None
    return _storage
