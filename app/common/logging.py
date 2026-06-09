from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.common.config import Settings


def configure_logging(settings: Settings) -> None:
    level = logging.getLevelNamesMapping().get(settings.LOG_LEVEL.upper(), logging.INFO)

    # Route stdlib logging through a plain stream handler so structlog's final
    # renderer is the only thing that formats the output (no extra decoration).
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,  # requires a stdlib-backed logger with .name
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if settings.LOG_FORMAT == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),  # stdlib loggers have .name
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
