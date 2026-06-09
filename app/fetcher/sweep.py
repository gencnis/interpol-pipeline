from __future__ import annotations

import string
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.common.config import Settings
from app.common.logging import get_logger
from app.fetcher.client import InterpolClient

log = get_logger(__name__)

_AGE_BUCKETS: list[tuple[int, int]] = [
    (0, 17),
    (18, 30),
    (31, 40),
    (41, 50),
    (51, 60),
    (61, 100),
]


# ---------------------------------------------------------------------------
# Filter dimensions
# ---------------------------------------------------------------------------


class FilterDimension(ABC):
    """One axis along which a result-set can be subdivided."""

    @abstractmethod
    def subdivisions(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Return a list of filter dicts, each augmenting *filters* with one sub-value."""


class AgeBucketDimension(FilterDimension):
    def __init__(self, buckets: list[tuple[int, int]] | None = None) -> None:
        self._buckets = buckets if buckets is not None else _AGE_BUCKETS

    def subdivisions(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return [{**filters, "ageMin": lo, "ageMax": hi} for lo, hi in self._buckets]


class ValueDimension(FilterDimension):
    def __init__(self, param: str, values: list[str]) -> None:
        self._param = param
        self._values = values

    def subdivisions(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return [{**filters, self._param: v} for v in self._values]


class LetterDimension(FilterDimension):
    """Subdivides by the first letter of a name field (A–Z)."""

    def __init__(self, param: str) -> None:
        self._param = param

    def subdivisions(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return [{**filters, self._param: c} for c in string.ascii_uppercase]


# ---------------------------------------------------------------------------
# _links.last page-count helper
# ---------------------------------------------------------------------------


def _parse_last_page(data: dict[str, Any]) -> int:
    """Extract the last-page number from ``_links.last.href``.

    Returns 1 when the field is absent (single-page result).

    >>> _parse_last_page({"_links": {"last": {"href": "/red?page=4&resultPerPage=20"}}})
    4
    >>> _parse_last_page({})
    1
    """
    try:
        href = data["_links"]["last"]["href"]
        return int(parse_qs(urlparse(href).query)["page"][0])
    except (KeyError, IndexError, ValueError, TypeError):
        return 1


# ---------------------------------------------------------------------------
# SweepStrategy
# ---------------------------------------------------------------------------


class SweepStrategy:
    """Recursively narrows Interpol filter slices to stay under the API cap.

    Algorithm:
        _descend(filters, depth):
            fetch page 1 → total
            if total <= CAP or no dimensions left:
                paginate this slice (using _links.last for page count)
            else:
                for each value of DIMENSIONS[depth]:
                    _descend(filters | value, depth + 1)

    Dedup is maintained via a per-sweep ``seen`` set keyed on ``entity_id``.
    """

    def __init__(
        self,
        client: InterpolClient,
        settings: Settings,
        dimensions: list[FilterDimension] | None = None,
    ) -> None:
        self._client = client
        self._cap = settings.INTERPOL_CAP
        self._dimensions: list[FilterDimension] = dimensions if dimensions is not None else [
            AgeBucketDimension(),
            ValueDimension("sexId", ["M", "F", "U"]),
            ValueDimension("arrestWarrantCountryId", settings.FETCH_ARREST_WARRANT_COUNTRIES),
            ValueDimension("nationality", settings.FETCH_NATIONALITIES),
            LetterDimension("forename"),
            LetterDimension("name"),
        ]
        self._seen: set[str] = set()

    def sweep(self) -> Iterator[dict[str, Any]]:
        """Entry point. Resets dedup state and yields all reachable notices."""
        self._seen = set()
        log.info("sweep.start", cap=self._cap, dimensions=len(self._dimensions))
        yield from self._descend({}, 0)
        log.info("sweep.done", unique=len(self._seen))

    def _descend(self, filters: dict[str, Any], depth: int) -> Iterator[dict[str, Any]]:
        first_page = self._client.fetch_page(filters, 1)
        total = first_page.get("total", 0)

        if total == 0:
            return

        if total <= self._cap:
            log.debug("sweep.paginate", total=total, depth=depth, filters=filters)
            yield from self._paginate(filters, first_page)
            return

        # Find the next non-empty dimension and recurse into it.
        for d in range(depth, len(self._dimensions)):
            subs = self._dimensions[d].subdivisions(filters)
            if subs:
                log.info(
                    "sweep.subdivide",
                    total=total,
                    depth=d,
                    dim=type(self._dimensions[d]).__name__,
                    branches=len(subs),
                    filters=filters,
                )
                for sub in subs:
                    yield from self._descend(sub, d + 1)
                return

        # All dimensions exhausted — paginate best-effort (may miss overflow).
        log.warning("sweep.overflow", total=total, cap=self._cap, filters=filters)
        yield from self._paginate(filters, first_page)

    def _paginate(self, filters: dict[str, Any], first_page: dict[str, Any]) -> Iterator[dict[str, Any]]:
        last_page = _parse_last_page(first_page)

        def _emit(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
            for notice in data.get("_embedded", {}).get("notices", []):
                nid = notice.get("entity_id", "")
                if nid and nid not in self._seen:
                    self._seen.add(nid)
                    yield notice
                elif nid:
                    log.info("sweep.dedup_hit", notice_id=nid, filters=filters)

        yield from _emit(first_page)
        for page in range(2, last_page + 1):
            log.info("sweep.page", page=page, last_page=last_page, filters=filters)
            yield from _emit(self._client.fetch_page(filters, page))
