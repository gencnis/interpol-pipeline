from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app.common.config import Settings
from app.fetcher.client import InterpolClient
from app.fetcher.publisher import FetchPublisher, _build_payload
from app.fetcher.sweep import SweepStrategy

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _notice(entity_id: str, nationality: str = "TR") -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "forename": "Jane",
        "name": "Doe",
        "nationalities": [nationality],
        "sex_id": "F",
        "date_of_birth": "1985/06/01",
        "thumbnail_url": None,
        "_links": {"self": {"href": "https://example.com"}},
    }


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "FETCH_NATIONALITIES": ["TR"],
        "FETCH_ARREST_WARRANT_COUNTRIES": ["TR"],
        "FETCH_RESULT_PER_PAGE": 20,
        "HTTP_MAX_RETRIES": 1,
        "HTTP_BACKOFF_BASE_SECONDS": 0.0,
        "INTERPOL_CAP": 160,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# InterpolClient — HTTP layer
# ---------------------------------------------------------------------------

class TestInterpolClient:
    def test_fetch_page_sends_correct_params(self) -> None:
        client = InterpolClient(_settings())
        fake_response = {"total": 5, "_embedded": {"notices": []}, "_links": {}}
        with patch.object(client, "_request", return_value=fake_response) as mock_req:
            result = client.fetch_page({"nationality": "TR"}, page=3)
        sent = mock_req.call_args[1]["params"]
        assert sent["nationality"] == "TR"
        assert sent["page"] == 3
        assert sent["resultPerPage"] == 20
        assert result is fake_response

    def test_fetch_total_returns_total_field(self) -> None:
        client = InterpolClient(_settings())
        ret = {"total": 42, "_embedded": {"notices": []}}
        with patch.object(client, "_request", return_value=ret):
            assert client.fetch_total({"nationality": "US"}) == 42

    def test_fetch_total_returns_zero_when_field_absent(self) -> None:
        client = InterpolClient(_settings())
        with patch.object(client, "_request", return_value={"_embedded": {"notices": []}}):
            assert client.fetch_total({}) == 0


# ---------------------------------------------------------------------------
# _build_payload
# ---------------------------------------------------------------------------

class TestBuildPayload:
    def test_fields_present(self) -> None:
        notice = _notice("2021/999", "DE")
        notice["thumbnail_url"] = "https://img.example.com/photo.jpg"
        payload = _build_payload(notice, "cycle-abc")
        assert payload["notice_id"] == "2021/999"
        assert payload["nationalities"] == ["DE"]
        assert payload["thumbnail_url"] == "https://img.example.com/photo.jpg"
        assert payload["cycle_id"] == "cycle-abc"
        assert "_links" not in payload

    def test_null_thumbnail_propagated(self) -> None:
        assert _build_payload(_notice("2021/1"), "c1")["thumbnail_url"] is None


# ---------------------------------------------------------------------------
# FetchPublisher
# ---------------------------------------------------------------------------

class TestFetchPublisher:
    def _publisher(self, notices: list[dict[str, Any]]) -> tuple[FetchPublisher, MagicMock]:
        mq = MagicMock()
        sweep = MagicMock(spec=SweepStrategy)
        sweep.sweep.return_value = iter(notices)
        return FetchPublisher(sweep, mq, _settings()), mq

    def test_publishes_upsert_per_notice_then_manifest(self) -> None:
        publisher, mq = self._publisher([_notice("2021/42", "FR")])
        result = publisher.run_cycle()

        assert result.published == 1
        assert result.errors == 0
        assert mq.publish.call_count == 2  # upsert + manifest

        upsert_key, upsert_payload = mq.publish.call_args_list[0][0]
        assert upsert_key == "notice.upsert"
        assert upsert_payload["notice_id"] == "2021/42"
        assert "cycle_id" in upsert_payload

        manifest_key, manifest_payload = mq.publish.call_args_list[1][0]
        assert manifest_key == "cycle.complete"
        assert "2021/42" in manifest_payload["notice_ids"]
        assert manifest_payload["total"] == 1

    def test_publish_error_increments_error_count(self) -> None:
        publisher, mq = self._publisher([_notice("2021/1")])
        mq.publish.side_effect = [RuntimeError("connection lost"), None]
        result = publisher.run_cycle()
        assert result.errors == 1
        assert result.published == 0

    def test_manifest_always_emitted_even_on_errors(self) -> None:
        publisher, mq = self._publisher([_notice("2021/1")])
        mq.publish.side_effect = [RuntimeError("boom"), None]
        publisher.run_cycle()
        assert mq.publish.call_args_list[-1][0][0] == "cycle.complete"
