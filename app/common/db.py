from __future__ import annotations

import contextlib
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.common.logging import get_logger

log = get_logger(__name__)

# Resolved at import time; override via env var for Docker (where migrations
# are copied to /migrations rather than living next to the installed package).
_MIGRATIONS_PATH = os.environ.get(
    "ALEMBIC_MIGRATIONS_PATH",
    str(Path(__file__).parent.parent.parent / "migrations"),
)


def get_engine(settings: Any) -> Engine:
    return create_engine(settings.POSTGRES_SYNC_DSN, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a transactional session; commit on success, rollback on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_migrations(settings: Any) -> None:
    """Run alembic upgrade head programmatically."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", _MIGRATIONS_PATH)
    cfg.set_main_option("sqlalchemy.url", settings.POSTGRES_SYNC_DSN)
    log.info("db.migrate_start", path=_MIGRATIONS_PATH)
    command.upgrade(cfg, "head")
    log.info("db.migrate_done")
