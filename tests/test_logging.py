from __future__ import annotations

import pytest

from app.common.config import Settings
from app.common.logging import configure_logging, get_logger


def test_configure_json_info_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging(Settings())
    log = get_logger("test.json")
    log.info("hello-json")  # must not raise AttributeError from add_logger_name
    captured = capsys.readouterr()
    assert "hello-json" in captured.out


def test_configure_pretty_info_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "pretty")
    configure_logging(Settings())
    log = get_logger("test.pretty")
    log.info("hello-pretty")  # must not raise AttributeError from add_logger_name
    captured = capsys.readouterr()
    assert "hello-pretty" in captured.out


def test_logger_has_standard_methods() -> None:
    configure_logging(Settings())
    log = get_logger("test")
    assert hasattr(log, "info")
    assert hasattr(log, "warning")
    assert hasattr(log, "error")
