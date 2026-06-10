"""Initial schema: notices + notice_history

Revision ID: 001
Revises:
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enum types are created automatically by op.create_table below.
    # Do NOT also call postgresql.ENUM(...).create() here — that causes a
    # DuplicateObject error because op.create_table fires its own CREATE TYPE.
    # Alembic's alembic_version tracking is the idempotency guard (migration
    # 001 only runs once per database).

    # --- notices -------------------------------------------------------------
    op.create_table(
        "notices",
        sa.Column("notice_id", sa.String(), primary_key=True),
        sa.Column("forename", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("sex_id", sa.String(8), nullable=True),
        sa.Column("date_of_birth", sa.Text(), nullable=True),
        sa.Column(
            "nationalities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "arrest_warrant_countries",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("charge_text", sa.Text(), nullable=True),
        sa.Column("thumbnail_object_key", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "withdrawn", name="notice_status"),
            nullable=False,
        ),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_notices_status", "notices", ["status"])
    op.create_index("ix_notices_last_changed_at", "notices", ["last_changed_at"])
    op.create_index(
        "ix_notices_nationalities",
        "notices",
        ["nationalities"],
        postgresql_using="gin",
    )

    # --- notice_history ------------------------------------------------------
    op.create_table(
        "notice_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "notice_id",
            sa.String(),
            sa.ForeignKey("notices.notice_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "change_type",
            sa.Enum("created", "updated", "withdrawn", name="change_type"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("diff", postgresql.JSONB(), nullable=True),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_notice_history_notice_id", "notice_history", ["notice_id"])
    op.create_index("ix_notice_history_change_type", "notice_history", ["change_type"])


def downgrade() -> None:
    op.drop_table("notice_history")
    op.drop_table("notices")
    op.execute("DROP TYPE IF EXISTS change_type")
    op.execute("DROP TYPE IF EXISTS notice_status")
