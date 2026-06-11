from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class HistoryItem(BaseModel):
    version: int
    change_type: str
    diff: dict[str, Any] | None = None
    recorded_at: datetime


class NoticeItem(BaseModel):
    notice_id: str
    forename: str | None = None
    name: str | None = None
    status: str
    nationalities: list[Any] = []
    thumbnail_url: str | None = None
    last_changed_at: datetime


class NoticeDetail(BaseModel):
    notice_id: str
    forename: str | None = None
    name: str | None = None
    sex_id: str | None = None
    date_of_birth: str | None = None
    nationalities: list[Any] = []
    arrest_warrant_countries: list[Any] = []
    charge_text: str | None = None
    status: str
    thumbnail_url: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime
    history: list[HistoryItem] = []


class AlertItem(BaseModel):
    id: int
    notice_id: str
    notice_name: str | None = None
    version: int
    change_type: str
    diff: dict[str, Any] | None = None
    recorded_at: datetime


class PagedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
