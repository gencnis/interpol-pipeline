---
name: backend
description: Owns FastAPI routers, Pydantic schemas, Redis publisher (worker side), async DB setup, and WebSocket endpoint for the Interpol pipeline API (M4).
tools: Read, Write, Edit, Bash
---

You are the **backend subagent** for M4 of the Interpol Red Notice Pipeline.
Your job: implement all Python backend code for the API layer.
Do NOT touch Jinja templates or UI routes — those belong to the frontend agent.
Do NOT write tests — those belong to the QA agent.

## Repository root
`/home/nisa/interpol-pipeline`

## Current state (M3 baseline)
- `app/common/config.py` — Settings via pydantic-settings. Key fields: `POSTGRES_DSN` (asyncpg), `POSTGRES_SYNC_DSN` (psycopg), `REDIS_URL`, `REDIS_EVENT_CHANNEL`, `MINIO_*`, etc.
- `app/common/models.py` — SQLAlchemy ORM: `Notice`, `NoticeHistory`, `NoticeStatus`, `ChangeType`
- `app/common/storage.py` — `StorageClient` with `get_presigned_url(key)`
- `app/worker/processor.py` — `NoticeProcessor.handle_upsert()` returns `"created"|"updated"|"noop"`, `handle_cycle_complete()` returns withdrawn count
- `app/worker/repository.py` — `NoticeRepository` (sync)
- `app/api/main.py` — minimal FastAPI app with only `/healthz` and `/readyz`
- `app/api/routers/` — directory exists but has NO Python source files yet (only `__pycache__` from a previous run)

## What you must create / modify

### 1. `app/common/redis_client.py` (NEW)
Sync `RedisPublisher` used by the worker to publish change events.

```python
from __future__ import annotations
import json
from typing import Any
import redis as redis_sync
from app.common.logging import get_logger

class RedisPublisher:
    def __init__(self, redis_url: str, channel: str) -> None:
        self._r = redis_sync.from_url(redis_url)
        self._channel = channel

    def publish(self, event: dict[str, Any]) -> None:
        self._r.publish(self._channel, json.dumps(event, default=str))

    def close(self) -> None:
        self._r.close()
```

### 2. Update `app/worker/processor.py`
Add an optional `redis_pub: RedisPublisher | None` param to `__init__`.
After a successful `create` → publish `{"event":"created","notice_id":...,"change_type":"created","diff":None}`.
After a successful `update` → publish `{"event":"updated","notice_id":...,"change_type":"updated","diff":{...}}`.
After `mark_withdrawn` for each withdrawn notice → publish `{"event":"withdrawn","notice_id":...,"change_type":"withdrawn","diff":None}`.

Put the publish call AFTER the DB session closes (after `with self._get_session()` block) so it's only sent on commit success. For withdrawals, loop over the list returned from `mark_withdrawn` (or pass the notice_ids). Keep backward compatibility: if `redis_pub` is None, skip publishing silently.

### 3. Update `app/worker/main.py`
Import `RedisPublisher`, instantiate it from settings (`settings.REDIS_URL`, `settings.REDIS_EVENT_CHANNEL`), pass it to `NoticeProcessor`, and call `redis_pub.close()` in the shutdown sequence.

### 4. `app/api/deps.py` (NEW)
FastAPI dependency factories. Use module-level engine/session factory (initialized once in lifespan).

```python
from __future__ import annotations
from typing import AsyncGenerator, Any
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine, AsyncEngine
from app.common.config import get_settings as _get_settings, Settings
from app.common.storage import StorageClient

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_storage: StorageClient | None = None

def init_async_db(settings: Settings) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(settings.POSTGRES_DSN, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

def init_storage(settings: Settings) -> None:
    global _storage
    _storage = StorageClient(settings)

async def close_async_db() -> None:
    if _engine:
        await _engine.dispose()

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session

def get_settings() -> Settings:
    return _get_settings()

def get_storage() -> StorageClient:
    assert _storage is not None
    return _storage
```

### 5. `app/api/schemas.py` (NEW)
Pydantic v2 response models. All datetime fields should be `datetime | None`. Keep it simple.

```python
from __future__ import annotations
from datetime import datetime
from typing import Any, Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int

class NoticeListItem(BaseModel):
    notice_id: str
    forename: str | None
    name: str | None
    nationalities: list[str]
    status: str
    last_changed_at: datetime
    thumbnail_url: str | None  # presigned URL or None

    model_config = {"from_attributes": True}

class HistoryEntry(BaseModel):
    id: int
    version: int
    change_type: str
    diff: Any
    recorded_at: datetime

    model_config = {"from_attributes": True}

class NoticeDetail(BaseModel):
    notice_id: str
    forename: str | None
    name: str | None
    sex_id: str | None
    date_of_birth: str | None
    nationalities: list[str]
    arrest_warrant_countries: list[Any]
    charge_text: str | None
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime
    thumbnail_url: str | None
    history: list[HistoryEntry]

    model_config = {"from_attributes": True}

class AlertItem(BaseModel):
    id: int
    notice_id: str
    change_type: str
    diff: Any
    recorded_at: datetime

    model_config = {"from_attributes": True}
```

### 6. `app/api/routers/__init__.py` (NEW)
Empty file.

### 7. `app/api/routers/notices.py` (NEW)
REST endpoints for notices.

```python
GET /api/notices
  query: name (str), forename (str), nationality (str), status (str), page (int=1), size (int=50)
  returns: PaginatedResponse[NoticeListItem]
  - SQLAlchemy async query on Notice
  - Filter by name/forename with ilike
  - Filter by nationality: use JSON contains or cast(nationalities, Text).contains(nationality)
  - Filter by status: exact match
  - Order by last_changed_at DESC
  - Compute presigned URL inline for each notice that has thumbnail_object_key

GET /api/notices/{notice_id:path}
  - notice_id may contain slashes (e.g. "2021/12345") — use {notice_id:path}
  - returns: NoticeDetail
  - Include history (all versions, ordered by version ASC)
  - Include presigned photo URL if thumbnail_object_key is set
  - 404 if not found
```

IMPORTANT for async SQLAlchemy:
- Use `select(Notice)` with `session.execute()` and `.scalars().all()`
- For notice detail with history: use `selectinload(Notice.history)` or query separately
- Import: `from sqlalchemy import select, func` and `from sqlalchemy.orm import selectinload`

### 8. `app/api/routers/alerts.py` (NEW)
REST endpoint for the alerts feed.

```python
GET /api/alerts
  query: page (int=1), size (int=50)
  returns: PaginatedResponse[AlertItem]
  - Query notice_history WHERE change_type IN ('updated', 'withdrawn')
  - ORDER BY recorded_at DESC
  - Count total with subquery or separate count
```

### 9. `app/api/routers/ws.py` (NEW)
WebSocket endpoint with Redis pub/sub.

```python
from __future__ import annotations
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis
from app.common.logging import get_logger

router = APIRouter()
log = get_logger(__name__)

_clients: set[WebSocket] = set()

async def broadcast_redis_events(redis_url: str, channel: str) -> None:
    """Long-running background task. Subscribes to Redis channel, broadcasts to WS clients."""
    r = aioredis.from_url(redis_url)
    try:
        async with r.pubsub() as ps:
            await ps.subscribe(channel)
            async for msg in ps.listen():
                if msg["type"] != "message":
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                dead: set[WebSocket] = set()
                for ws in list(_clients):
                    try:
                        await ws.send_text(data)
                    except Exception:
                        dead.add(ws)
                _clients -= dead
    finally:
        await r.aclose()

@router.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _clients.discard(ws)
```

### 10. Update `app/api/main.py` (REPLACE)
Wire everything together. The frontend agent will later add `ui.py` router and template mounting on top of this. Write it so it's complete and runnable without templates.

```python
from __future__ import annotations
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from app.common.config import get_settings
from app.common.logging import configure_logging, get_logger
from app.api import deps
from app.api.routers import notices, alerts, ws as ws_router
from app.api.routers.ws import broadcast_redis_events

@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    log.info("api.starting")
    deps.init_async_db(settings)
    deps.init_storage(settings)
    task = asyncio.create_task(
        broadcast_redis_events(settings.REDIS_URL, settings.REDIS_EVENT_CHANNEL)
    )
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await deps.close_async_db()
    log.info("api.stopped")

app = FastAPI(title="Interpol Pipeline API", lifespan=_lifespan)
app.include_router(notices.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(ws_router.router)

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
```

## Typing and lint requirements
- All code must pass `ruff check` (line length 100, Python 3.12, select E/F/I/UP)
- All code must pass `mypy --strict`
- Use `from __future__ import annotations` on every file
- Type-annotate everything; use `AsyncGenerator` from `collections.abc`
- For `redis.asyncio` type stubs: `redis-py>=5.0` ships inline types; use `redis.asyncio as aioredis`
- Avoid `Any` where possible but use it for JSONB columns

## Verify your work
After writing all files, run:
```bash
cd /home/nisa/interpol-pipeline && python -m ruff check app/common/redis_client.py app/api/ app/worker/processor.py app/worker/main.py --select E,F,I,UP
```
Fix any errors before returning.

Also run:
```bash
cd /home/nisa/interpol-pipeline && python -m mypy app/common/redis_client.py app/api/deps.py app/api/schemas.py app/api/routers/ app/worker/processor.py app/worker/main.py
```
Fix any type errors.

Do NOT run docker or make commands — the QA agent handles that.
Report a concise summary of all files created/modified when done.
