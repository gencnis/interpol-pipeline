from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):  # noqa: UP046
    items: list[T]
    total: int
    page: int
    size: int


class NoticeListItem(BaseModel):
    notice_id: str
    forename: str | None
    name: str | None
    nationalities: list[str]
    status: str
    last_changed_at: datetime
    thumbnail_url: str | None  # presigned URL or None

    model_config = {"from_attributes": True}


class HistoryEntry(BaseModel):
    id: int
    version: int
    change_type: str
    diff: Any
    recorded_at: datetime

    model_config = {"from_attributes": True}


class NoticeDetail(BaseModel):
    notice_id: str
    forename: str | None
    name: str | None
    sex_id: str | None
    date_of_birth: str | None
    nationalities: list[str]
    arrest_warrant_countries: list[Any]
    charge_text: str | None
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime
    thumbnail_url: str | None
    history: list[HistoryEntry]

    model_config = {"from_attributes": True}


class AlertItem(BaseModel):
    id: int
    notice_id: str
    change_type: str
    diff: Any
    recorded_at: datetime

    model_config = {"from_attributes": True}
