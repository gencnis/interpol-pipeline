from __future__ import annotations

import pytest

from app.common.config import Settings


def test_nationalities_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_NATIONALITIES", "TR,US,DE")
    assert Settings().FETCH_NATIONALITIES == ["TR", "US", "DE"]


def test_nationalities_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_NATIONALITIES", " TR , US , DE ")
    assert Settings().FETCH_NATIONALITIES == ["TR", "US", "DE"]


def test_defaults_are_valid() -> None:
    s = Settings()
    assert s.LOG_FORMAT in ("json", "pretty")
    assert s.FETCH_RESULT_PER_PAGE > 0
    assert s.HTTP_MAX_RETRIES > 0
    assert s.FETCH_INTERVAL_SECONDS > 0


def test_log_format_pretty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "pretty")
    assert Settings().LOG_FORMAT == "pretty"


def test_minio_secure_default() -> None:
    assert Settings().MINIO_SECURE is False
