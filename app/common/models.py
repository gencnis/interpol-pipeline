from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NoticeStatus(enum.StrEnum):
    active = "active"
    withdrawn = "withdrawn"


class ChangeType(enum.StrEnum):
    created = "created"
    updated = "updated"
    withdrawn = "withdrawn"


class Notice(Base):
    __tablename__ = "notices"

    notice_id: Mapped[str] = mapped_column(String, primary_key=True)
    forename: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    sex_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    date_of_birth: Mapped[str | None] = mapped_column(Text, nullable=True)
    nationalities: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    arrest_warrant_countries: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    charge_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(NoticeStatus, name="notice_status"),
        nullable=False,
        server_default=NoticeStatus.active.value,
    )
    raw_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    first_seen_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_changed_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    history: Mapped[list[NoticeHistory]] = relationship(
        "NoticeHistory", back_populates="notice", order_by="NoticeHistory.version"
    )

    __table_args__ = (
        Index("ix_notices_status", "status"),
        Index("ix_notices_last_changed_at", "last_changed_at"),
        Index("ix_notices_nationalities", "nationalities", postgresql_using="gin"),
    )


class NoticeHistory(Base):
    __tablename__ = "notice_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notice_id: Mapped[str] = mapped_column(
        String, ForeignKey("notices.notice_id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(
        Enum(ChangeType, name="change_type"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    diff: Mapped[Any] = mapped_column(JSONB, nullable=True)
    valid_from: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_to: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    notice: Mapped[Notice] = relationship("Notice", back_populates="history")

    __table_args__ = (
        Index("ix_notice_history_notice_id", "notice_id"),
        Index("ix_notice_history_change_type", "change_type"),
    )
