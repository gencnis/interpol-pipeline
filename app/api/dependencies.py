from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.config import Settings
from app.common.config import get_settings as _get_settings
from app.common.storage import StorageClient

_session_factory: async_sessionmaker[AsyncSession] | None = None
_storage_client: StorageClient | None = None


def setup_db(factory: async_sessionmaker[AsyncSession]) -> None:
    global _session_factory
    _session_factory = factory


def setup_storage(client: StorageClient) -> None:
    global _storage_client
    _storage_client = client


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    assert _session_factory is not None, "DB session factory not initialised"
    async with _session_factory() as session:
        yield session


def get_storage() -> StorageClient:
    assert _storage_client is not None, "Storage client not initialised"
    return _storage_client


def get_settings() -> Settings:
    return _get_settings()
