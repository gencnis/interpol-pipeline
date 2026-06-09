from __future__ import annotations

import time
from typing import Any

from curl_cffi.requests import RequestsError, Session as CffiSession

from app.common.config import Settings
from app.common.logging import get_logger

log = get_logger(__name__)


class InterpolClient:
    """Thin HTTP client for the Interpol public web service.

    Uses curl_cffi with Chrome TLS impersonation to pass Akamai's JA3/JA4
    fingerprint check.  Sweep/pagination logic lives in SweepStrategy.
    """

    _LIST_PATH = "/notices/v1/red"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = CffiSession(
            impersonate=settings.INTERPOL_IMPERSONATE,
            headers={
                "Accept": "application/json",
                "Referer": settings.INTERPOL_REFERER,
                "Origin": settings.INTERPOL_ORIGIN,
            },
        )

    def fetch_page(self, filters: dict[str, Any], page: int) -> dict[str, Any]:
        """Fetch one page of the notice list for the given filter params."""
        return self._request(
            "GET",
            self._LIST_PATH,
            params={**filters, "page": page, "resultPerPage": self._settings.FETCH_RESULT_PER_PAGE},
        )

    def fetch_total(self, filters: dict[str, Any]) -> int:
        """Return the total result count for a filter combination (cheap page-1 probe)."""
        return self.fetch_page(filters, 1).get("total", 0)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = self._settings.INTERPOL_BASE_URL.rstrip("/") + path
        last_exc: Exception | None = None
        for attempt in range(self._settings.HTTP_MAX_RETRIES):
            try:
                resp = self._http.request(method, url, **kwargs)
                if resp.status_code == 200:
                    return resp.json()  # type: ignore[no-any-return]
                if resp.status_code == 403 or resp.status_code >= 500:
                    # 403: Akamai fingerprint may be drifting — retry, degrade gracefully on exhaustion
                    # 5xx: transient server errors
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    log.warning(
                        "fetcher.http_retryable",
                        status=resp.status_code,
                        attempt=attempt + 1,
                        url=url,
                    )
                else:
                    raise RuntimeError(f"HTTP {resp.status_code} (non-retryable) path={path}")
            except RequestsError as exc:
                last_exc = exc
            delay = self._settings.HTTP_BACKOFF_BASE_SECONDS * (2**attempt)
            log.warning("fetcher.http_retry", attempt=attempt + 1, delay=delay, error=str(last_exc))
            time.sleep(delay)
        log.error("fetcher.http_failed", attempts=self._settings.HTTP_MAX_RETRIES, error=str(last_exc))
        raise RuntimeError(
            f"HTTP request failed after {self._settings.HTTP_MAX_RETRIES} attempts"
        ) from last_exc

    def close(self) -> None:
        self._http.close()
