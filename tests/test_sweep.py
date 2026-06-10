from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.common.config import Settings
from app.fetcher.client import InterpolClient
from app.fetcher.sweep import (
    AgeBucketDimension,
    LetterDimension,
    SweepStrategy,
    ValueDimension,
    _parse_last_page,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _notice(
    entity_id: str,
    *,
    nationalities: list[str] | None = None,
    sex_id: str = "M",
    age: int = 30,
    arrest_warrant_countries: list[str] | None = None,
    forename: str = "Alice",
    name: str = "Smith",
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "forename": forename,
        "name": name,
        "nationalities": nationalities or ["TR"],
        "sex_id": sex_id,
        "age": age,
        "arrest_warrant_countries": arrest_warrant_countries or ["TR"],
        "thumbnail_url": None,
    }


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "FETCH_NATIONALITIES": ["TR"],
        "FETCH_ARREST_WARRANT_COUNTRIES": ["TR"],
        "FETCH_RESULT_PER_PAGE": 10,
        "HTTP_MAX_RETRIES": 1,
        "HTTP_BACKOFF_BASE_SECONDS": 0.0,
        "INTERPOL_CAP": 5,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# FakeInterpolBackend
# ---------------------------------------------------------------------------

class FakeInterpolBackend:
    """Simulates the Interpol list endpoint from a real notice list.

    Filters notices by the query params present in each request, returns an
    accurate ``total``, paginates the slice, and builds a ``_links.last`` href
    that ``_parse_last_page`` can decode — all without network access.

    Tracks every call in ``self.calls`` so tests can assert on request params.
    """

    def __init__(self, notices: list[dict[str, Any]], page_size: int = 10) -> None:
        self._notices = notices
        self._page_size = page_size
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        params = kwargs.get("params", {})
        self.calls.append(dict(params))

        filtered = self._filter(params)
        total = len(filtered)
        page = int(params.get("page", 1))
        per_page = int(params.get("resultPerPage", self._page_size))

        start = (page - 1) * per_page
        page_notices = filtered[start : start + per_page]
        last_page = max(1, (total + per_page - 1) // per_page)

        links: dict[str, Any] = {}
        if total > 0:
            links["last"] = {"href": f"/notices/v1/red?page={last_page}&resultPerPage={per_page}"}

        return {
            "total": total,
            "_embedded": {"notices": page_notices},
            "_links": links,
        }

    def _filter(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        result = list(self._notices)
        if nat := params.get("nationality"):
            result = [n for n in result if nat in n.get("nationalities", [])]
        if sex := params.get("sexId"):
            result = [n for n in result if n.get("sex_id") == sex]
        if (lo := params.get("ageMin")) is not None:
            result = [n for n in result if n.get("age", 0) >= int(lo)]
        if (hi := params.get("ageMax")) is not None:
            result = [n for n in result if n.get("age", 999) <= int(hi)]
        if country := params.get("arrestWarrantCountryId"):
            result = [n for n in result if country in n.get("arrest_warrant_countries", [])]
        if forename := params.get("forename"):
            result = [
                n for n in result if (n.get("forename") or "").upper().startswith(forename.upper())
            ]
        if name_val := params.get("name"):
            result = [
                n for n in result if (n.get("name") or "").upper().startswith(name_val.upper())
            ]
        return result


def _sweep(
    notices: list[dict[str, Any]], dimensions: list[Any], **setting_overrides: Any
) -> list[dict[str, Any]]:
    """Helper: run SweepStrategy over FakeInterpolBackend and return all noticed yielded."""
    settings = _settings(**setting_overrides)
    client = InterpolClient(settings)
    backend = FakeInterpolBackend(notices, page_size=settings.FETCH_RESULT_PER_PAGE)
    with patch.object(client, "_request", side_effect=backend):
        strategy = SweepStrategy(client, settings, dimensions=dimensions)
        return list(strategy.sweep())


# ---------------------------------------------------------------------------
# _parse_last_page
# ---------------------------------------------------------------------------

class TestParseLinkLastPage:
    def test_extracts_page_number_from_href(self) -> None:
        href = "/notices/v1/red?nationality=TR&page=4&resultPerPage=20"
        data = {"_links": {"last": {"href": href}}}
        assert _parse_last_page(data) == 4

    def test_returns_one_when_links_absent(self) -> None:
        assert _parse_last_page({}) == 1

    def test_returns_one_when_last_absent(self) -> None:
        assert _parse_last_page({"_links": {}}) == 1

    def test_returns_one_on_malformed_href(self) -> None:
        assert _parse_last_page({"_links": {"last": {"href": "not-a-url"}}}) == 1


# ---------------------------------------------------------------------------
# FilterDimension.subdivisions
# ---------------------------------------------------------------------------

class TestFilterDimensions:
    def test_age_bucket_dimension_augments_filters(self) -> None:
        dim = AgeBucketDimension(buckets=[(0, 17), (18, 30)])
        subs = dim.subdivisions({"nationality": "TR"})
        assert subs == [
            {"nationality": "TR", "ageMin": 0, "ageMax": 17},
            {"nationality": "TR", "ageMin": 18, "ageMax": 30},
        ]

    def test_value_dimension_augments_filters(self) -> None:
        dim = ValueDimension("sexId", ["M", "F", "U"])
        subs = dim.subdivisions({"ageMin": 18, "ageMax": 30})
        assert [s["sexId"] for s in subs] == ["M", "F", "U"]
        assert all(s["ageMin"] == 18 for s in subs)

    def test_letter_dimension_produces_26_values(self) -> None:
        dim = LetterDimension("forename")
        subs = dim.subdivisions({})
        assert len(subs) == 26
        assert subs[0]["forename"] == "A"
        assert subs[-1]["forename"] == "Z"

    def test_empty_value_dimension_produces_no_subs(self) -> None:
        dim = ValueDimension("nationality", [])
        assert dim.subdivisions({}) == []


# ---------------------------------------------------------------------------
# SweepStrategy — core behaviour
# ---------------------------------------------------------------------------

class TestSweepStrategyUnderCap:
    def test_slice_under_cap_paginates_only_no_subdivision(self) -> None:
        """total ≤ CAP: sweep must paginate the slice without trying any dimension."""
        notices = [_notice(f"id/{i}") for i in range(3)]
        # Single dimension that would subdivide if reached
        dims = [ValueDimension("nationality", ["TR", "US"])]
        results = _sweep(notices, dims, INTERPOL_CAP=5)
        assert len(results) == 3
        assert {n["entity_id"] for n in results} == {f"id/{i}" for i in range(3)}

    def test_empty_slice_yields_nothing(self) -> None:
        results = _sweep([], [ValueDimension("nationality", ["TR"])], INTERPOL_CAP=5)
        assert results == []


class TestSweepStrategySubdivision:
    def test_over_cap_triggers_first_dimension(self) -> None:
        """total > CAP: the first dimension must be tried; each sub-slice queried."""
        # 8 notices split evenly across two nationalities; CAP=5
        notices = (
            [_notice(f"TR/{i}", nationalities=["TR"]) for i in range(4)]
            + [_notice(f"US/{i}", nationalities=["US"]) for i in range(4)]
        )
        dims = [ValueDimension("nationality", ["TR", "US"])]
        backend = FakeInterpolBackend(notices, page_size=10)
        settings = _settings(INTERPOL_CAP=5)
        client = InterpolClient(settings)
        with patch.object(client, "_request", side_effect=backend):
            strategy = SweepStrategy(client, settings, dimensions=dims)
            results = list(strategy.sweep())

        assert len(results) == 8
        # Both nationality sub-slices must have been queried
        queried_nats = {c.get("nationality") for c in backend.calls if c.get("nationality")}
        assert queried_nats == {"TR", "US"}

    def test_two_level_subdivision(self) -> None:
        """When the first sub-slice is still > CAP, descend to the second dimension."""
        # 12 notices: all age 25 (hits 18–30 bucket), 6 sex=M and 6 sex=F; CAP=5
        notices = (
            [_notice(f"M/{i}", sex_id="M", age=25) for i in range(6)]
            + [_notice(f"F/{i}", sex_id="F", age=25) for i in range(6)]
        )
        dims = [
            AgeBucketDimension(buckets=[(18, 30)]),   # first split: one age bucket
            ValueDimension("sexId", ["M", "F", "U"]),  # second split: sex
        ]
        results = _sweep(notices, dims, INTERPOL_CAP=5)
        assert len(results) == 12

    def test_skips_empty_dimensions_to_find_next_viable_one(self) -> None:
        """A dimension with no values is skipped; the next non-empty one is used."""
        notices = [_notice(f"id/{i}", nationalities=["TR"]) for i in range(8)]
        dims = [
            ValueDimension("sexId", []),              # empty — skipped
            ValueDimension("nationality", ["TR"]),     # non-empty — used
        ]
        results = _sweep(notices, dims, INTERPOL_CAP=5)
        assert len(results) == 8

    def test_all_dimensions_exhausted_paginates_best_effort(self) -> None:
        """When no dimension is left and total > CAP, paginate anyway without crashing."""
        notices = [_notice(f"id/{i}") for i in range(8)]
        results = _sweep(notices, dimensions=[], INTERPOL_CAP=5)
        # All 8 notices must be returned (no dimension to subdivide, best-effort paginate)
        assert len(results) == 8


class TestSweepStrategyPagination:
    def test_pagination_walks_all_pages_via_links_last(self) -> None:
        """Multi-page slice: every page must be fetched using _links.last."""
        # 15 notices, page_size=5 → 3 pages; CAP=20 (no subdivision)
        notices = [_notice(f"id/{i}") for i in range(15)]
        backend = FakeInterpolBackend(notices, page_size=5)
        settings = _settings(INTERPOL_CAP=20, FETCH_RESULT_PER_PAGE=5)
        client = InterpolClient(settings)
        with patch.object(client, "_request", side_effect=backend):
            strategy = SweepStrategy(client, settings, dimensions=[])
            results = list(strategy.sweep())

        assert len(results) == 15
        pages_fetched = [c["page"] for c in backend.calls]
        assert pages_fetched == [1, 2, 3]

    def test_single_page_slice_makes_one_request(self) -> None:
        notices = [_notice(f"id/{i}") for i in range(3)]
        backend = FakeInterpolBackend(notices, page_size=10)
        settings = _settings(INTERPOL_CAP=20, FETCH_RESULT_PER_PAGE=10)
        client = InterpolClient(settings)
        with patch.object(client, "_request", side_effect=backend):
            strategy = SweepStrategy(client, settings, dimensions=[])
            results = list(strategy.sweep())
        assert len(results) == 3
        assert len(backend.calls) == 1


class TestSweepStrategyDedup:
    def test_dedup_across_overlapping_slices(self) -> None:
        """A notice with multiple nationalities appears in two sub-slices but is yielded once."""
        # notice_shared has both TR and US nationalities → returned by both sub-slices
        notice_tr = _notice("TR/1", nationalities=["TR"])
        notice_us = _notice("US/1", nationalities=["US"])
        notice_shared = _notice("SHARED/1", nationalities=["TR", "US"])

        notices = [notice_tr, notice_us, notice_shared]
        # CAP=2 forces subdivision by nationality (total=3 > 2)
        dims = [ValueDimension("nationality", ["TR", "US"])]
        results = _sweep(notices, dims, INTERPOL_CAP=2)

        assert len(results) == 3  # not 4 (shared would be returned twice without dedup)
        ids = {n["entity_id"] for n in results}
        assert ids == {"TR/1", "US/1", "SHARED/1"}

    def test_dedup_within_single_paginated_slice(self) -> None:
        """entity_id appearing on two pages of the same slice is yielded only once."""
        # Construct a backend that returns the same notice on both pages
        notice = _notice("dup/1")
        backend_responses: list[dict[str, Any]] = [
            {
                "total": 2,
                "_embedded": {"notices": [notice]},
                "_links": {"last": {"href": "/r?page=2&resultPerPage=1"}},
            },
            {
                "total": 2,
                "_embedded": {"notices": [notice]},  # same notice again
                "_links": {"last": {"href": "/r?page=2&resultPerPage=1"}},
            },
        ]
        call_iter = iter(backend_responses)
        settings = _settings(INTERPOL_CAP=5)
        client = InterpolClient(settings)
        with patch.object(client, "_request", side_effect=lambda *a, **kw: next(call_iter)):
            strategy = SweepStrategy(client, settings, dimensions=[])
            results = list(strategy.sweep())
        assert len(results) == 1


class TestSweepStrategyFullSweep:
    def test_full_sweep_coverage_across_age_and_nationality(self) -> None:
        """End-to-end: age × nationality grid, each cell ≤ CAP, no notices lost."""
        notices = (
            [_notice(f"TR/young/{i}", nationalities=["TR"], age=20) for i in range(3)]
            + [_notice(f"TR/old/{i}", nationalities=["TR"], age=55) for i in range(3)]
            + [_notice(f"US/young/{i}", nationalities=["US"], age=20) for i in range(3)]
            + [_notice(f"US/old/{i}", nationalities=["US"], age=55) for i in range(3)]
        )
        dims = [
            AgeBucketDimension(buckets=[(18, 40), (41, 70)]),
            ValueDimension("nationality", ["TR", "US"]),
        ]
        # CAP=10 → top-level total=12 > 10 → subdivide by age
        # Each age bucket: total=6 > 5? No — set CAP=4 to force age→nationality descent
        results = _sweep(notices, dims, INTERPOL_CAP=4)
        assert len(results) == 12
        assert {n["entity_id"] for n in results} == {n["entity_id"] for n in notices}
