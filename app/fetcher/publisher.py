from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.common.config import Settings
from app.common.logging import get_logger
from app.common.mq import MQClient
from app.fetcher.sweep import SweepStrategy

log = get_logger(__name__)

_UPSERT_KEY = "notice.upsert"
_MANIFEST_KEY = "cycle.complete"


@dataclass
class CycleResult:
    cycle_id: str
    started_at: str
    finished_at: str
    published: int
    errors: int


class FetchPublisher:
    def __init__(self, sweep: SweepStrategy, mq: MQClient, settings: Settings) -> None:
        self._sweep = sweep
        self._mq = mq
        self._settings = settings

    def run_cycle(self) -> CycleResult:
        cycle_id = str(uuid.uuid4())
        started_at = datetime.now(UTC).isoformat()
        notice_ids: list[str] = []
        published = 0
        errors = 0

        log.info("fetcher.cycle_start", cycle_id=cycle_id)

        for notice in self._sweep.sweep():
            notice_id = notice.get("entity_id", "")
            try:
                self._mq.publish(_UPSERT_KEY, _build_payload(notice, cycle_id))
                notice_ids.append(notice_id)
                published += 1
            except Exception as exc:
                errors += 1
                log.error("fetcher.publish_error", notice_id=notice_id, error=str(exc))

        finished_at = datetime.now(UTC).isoformat()
        self._mq.publish(
            _MANIFEST_KEY,
            {
                "cycle_id": cycle_id,
                "notice_ids": notice_ids,
                "total": published,
                "errors": errors,
                # "ok" only when the sweep completed with zero publish errors;
                # the worker's withdrawal guard rejects anything else.
                "status": "ok" if errors == 0 else "partial",
                "started_at": started_at,
                "finished_at": finished_at,
            },
        )

        result = CycleResult(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            published=published,
            errors=errors,
        )
        log.info("fetcher.cycle_done", **result.__dict__)
        return result


def _build_payload(notice: dict[str, Any], cycle_id: str) -> dict[str, Any]:
    return {
        "notice_id": notice.get("entity_id", ""),
        "forename": notice.get("forename"),
        "name": notice.get("name"),
        "nationalities": notice.get("nationalities", []),
        "arrest_warrant_countries": (
            notice.get("arrest_warrant_countries")
            or notice.get("arrestWarrantCountries")
            or []
        ),
        "sex_id": notice.get("sex_id"),
        "date_of_birth": notice.get("date_of_birth"),
        "thumbnail_url": notice.get("thumbnail_url"),
        "cycle_id": cycle_id,
    }
