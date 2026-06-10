from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.storage import StorageClient


async def _get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        yield session


async def _get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


async def _get_storage(request: Request) -> StorageClient:
    return request.app.state.storage  # type: ignore[no-any-return]


DBSession = Annotated[AsyncSession, Depends(_get_db)]
AppSettings = Annotated[Settings, Depends(_get_settings)]
Storage = Annotated[StorageClient, Depends(_get_storage)]
