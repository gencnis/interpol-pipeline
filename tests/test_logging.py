from __future__ import annotations

import pytest

from app.common.config import Settings
from app.common.logging import configure_logging, get_logger


def test_configure_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging(Settings())
    assert get_logger("test.json") is not None


def test_configure_pretty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "pretty")
    configure_logging(Settings())
    assert get_logger("test.pretty") is not None


def test_logger_is_callable() -> None:
    configure_logging(Settings())
    log = get_logger("test")
    assert hasattr(log, "info")
    assert hasattr(log, "warning")
    assert hasattr(log, "error")
