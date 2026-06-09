from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app.common.config import Settings
from app.fetcher.client import InterpolClient
from app.fetcher.publisher import FetchPublisher, _build_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _page(notices: list[dict[str, Any]], total: int) -> dict[str, Any]:
    return {"total": total, "_embedded": {"notices": notices}}


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
        "FETCH_RESULT_PER_PAGE": 2,
        "HTTP_MAX_RETRIES": 1,
        "HTTP_BACKOFF_BASE_SECONDS": 0.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# InterpolClient pagination
# ---------------------------------------------------------------------------

class TestInterpolClientPagination:
    def test_single_page_returned_in_full(self) -> None:
        pages = [_page([_notice("2021/1"), _notice("2021/2")], total=2)]
        client = InterpolClient(_settings())
        it = iter(pages)
        with patch.object(client, "_request", side_effect=lambda *a, **kw: next(it)):
            result = list(client.sweep())
        assert [n["entity_id"] for n in result] == ["2021/1", "2021/2"]

    def test_paginates_to_second_page_when_total_exceeds_page_size(self) -> None:
        pages = [
            _page([_notice("2021/1"), _notice("2021/2")], total=3),
            _page([_notice("2021/3")], total=3),
        ]
        client = InterpolClient(_settings())
        it = iter(pages)
        with patch.object(client, "_request", side_effect=lambda *a, **kw: next(it)):
            result = list(client.sweep())
        assert len(result) == 3
        assert result[-1]["entity_id"] == "2021/3"

    def test_empty_first_page_yields_nothing(self) -> None:
        client = InterpolClient(_settings())
        with patch.object(client, "_request", return_value=_page([], total=0)):
            result = list(client.sweep())
        assert result == []

    def test_dedup_removes_cross_nationality_duplicates(self) -> None:
        settings = _settings(FETCH_NATIONALITIES=["TR", "US"])
        shared_notice = _notice("2021/1")
        client = InterpolClient(settings)
        with patch.object(
            client, "_request", return_value=_page([shared_notice], total=1)
        ):
            result = list(client.sweep())
        assert len(result) == 1  # not 2

    def test_no_dedup_for_distinct_ids(self) -> None:
        settings = _settings(FETCH_NATIONALITIES=["TR", "US"])
        pages = {
            "TR": _page([_notice("2021/1", "TR")], total=1),
            "US": _page([_notice("2021/2", "US")], total=1),
        }
        call_index = [0]
        nationalities = ["TR", "US"]

        def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nat = kwargs.get("params", {}).get("nationality", nationalities[call_index[0]])
            call_index[0] += 1
            return pages[nat]

        client = InterpolClient(settings)
        with patch.object(client, "_request", side_effect=fake_request):
            result = list(client.sweep())
        assert len(result) == 2


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
        payload = _build_payload(_notice("2021/1"), "c1")
        assert payload["thumbnail_url"] is None


# ---------------------------------------------------------------------------
# FetchPublisher
# ---------------------------------------------------------------------------

class TestFetchPublisher:
    def test_publishes_upsert_then_manifest(self) -> None:
        notice = _notice("2021/42", "FR")
        mq = MagicMock()
        client = MagicMock(spec=InterpolClient)
        client.sweep.return_value = iter([notice])
        publisher = FetchPublisher(client, mq, _settings())

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
        mq = MagicMock()
        mq.publish.side_effect = [RuntimeError("connection lost"), None]
        client = MagicMock(spec=InterpolClient)
        client.sweep.return_value = iter([_notice("2021/1")])
        publisher = FetchPublisher(client, mq, _settings())

        result = publisher.run_cycle()

        assert result.errors == 1
        assert result.published == 0

    def test_manifest_always_emitted_even_on_errors(self) -> None:
        mq = MagicMock()
        mq.publish.side_effect = [RuntimeError("boom"), None]
        client = MagicMock(spec=InterpolClient)
        client.sweep.return_value = iter([_notice("2021/1")])
        publisher = FetchPublisher(client, mq, _settings())

        publisher.run_cycle()

        manifest_call = mq.publish.call_args_list[-1]
        assert manifest_call[0][0] == "cycle.complete"
