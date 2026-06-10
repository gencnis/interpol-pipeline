from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    change_type: str
    diff: Any
    recorded_at: datetime
    valid_from: datetime
    valid_to: datetime | None


class NoticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notice_id: str
    forename: str | None
    name: str | None
    sex_id: str | None
    date_of_birth: str | None
    nationalities: Any
    arrest_warrant_countries: Any
    charge_text: str | None
    thumbnail_url: str | None = None
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime


class NoticeDetailOut(NoticeOut):
    history: list[HistoryOut] = []


class AlertOut(BaseModel):
    id: int
    notice_id: str
    version: int
    change_type: str
    diff: Any
    recorded_at: datetime
    forename: str | None = None
    name: str | None = None


class PagedOut(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    pages: int
