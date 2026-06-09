from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.common.config import Settings
from app.common.logging import get_logger

log = get_logger(__name__)


class InterpolClient:
    """HTTP client for the Interpol public web service.

    Sweeps a configured nationality list, paginates each to exhaustion,
    and deduplicates notice IDs that appear under multiple nationalities.

    >>> client = InterpolClient.__new__(InterpolClient)
    >>> client._deduplicate(["a", "b", "a"])  # doctest: +SKIP
    ['a', 'b']
    """

    _LIST_PATH = "/notices/v1/red"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.Client(
            base_url=settings.INTERPOL_BASE_URL,
            timeout=30.0,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            },
        )

    def sweep(self) -> Iterator[dict[str, Any]]:
        """Yield deduplicated notice summaries across all configured nationalities."""
        seen: set[str] = set()
        for nationality in self._settings.FETCH_NATIONALITIES:
            for notice in self._paginate(nationality):
                nid = notice.get("entity_id", "")
                if nid in seen:
                    continue
                seen.add(nid)
                yield notice

    def _paginate(self, nationality: str) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            data = self._fetch_list(nationality=nationality, page=page)
            notices = data.get("_embedded", {}).get("notices", [])
            if not notices:
                break
            yield from notices
            total = data.get("total", 0)
            if page * self._settings.FETCH_RESULT_PER_PAGE >= total:
                break
            page += 1

    def _fetch_list(self, nationality: str, page: int) -> dict[str, Any]:
        return self._request(
            "GET",
            self._LIST_PATH,
            params={
                "nationality": nationality,
                "page": page,
                "resultPerPage": self._settings.FETCH_RESULT_PER_PAGE,
            },
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._settings.HTTP_MAX_RETRIES):
            try:
                resp = self._http.request(method, path, **kwargs)
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_exc = exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
            delay = self._settings.HTTP_BACKOFF_BASE_SECONDS * (2**attempt)
            log.warning("fetcher.http_retry", attempt=attempt + 1, delay=delay, error=str(last_exc))
            time.sleep(delay)
        raise RuntimeError(
            f"HTTP request failed after {self._settings.HTTP_MAX_RETRIES} attempts"
        ) from last_exc

    def close(self) -> None:
        self._http.close()
