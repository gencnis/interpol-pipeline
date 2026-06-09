# PRD — Interpol Red Notice Pipeline

**Status:** Draft for build
**Owner:** Nisanur Genç
**Audience:** Claude Code CLI (primary implementer), reviewers, portfolio readers

---

## 1. Purpose & context

Build a containerized system that continuously ingests Interpol Red Notices, persists them durably, and surfaces changes to a live web interface. The system polls the Interpol public API on a schedule, queues each notice through a message broker, processes and stores it (including photos), and detects when a previously-seen notice has **changed** or been **withdrawn** — surfacing those as alarms in real time.

The defining requirement is treating change as first-class: not just "store the latest," but recording **what changed** between versions of a notice and exposing a live, auditable feed of those changes.

This project serves two goals at once:
1. **Portfolio piece** — demonstrates event-driven architecture, OOP, testing, and DevOps maturity.
2. **Deployable system** — runs end-to-end against the live Interpol public API via `docker compose up`.

---

## 2. Goals & non-goals

### Goals
- Continuously ingest Interpol Red Notices, persist them, and surface live changes.
- Detect and record **what changed** between versions of a notice (field-level diff + history).
- Detect **withdrawals** (notices that disappear from the active feed).
- Store and serve notice **photos** via object storage.
- Be fully configurable via environment without code changes.
- Be reproducible, tested, and CI-verified.

### Non-goals
- Facial recognition / biometric processing on photos.
- Auth / multi-tenant accounts (single internal tool).
- Yellow/UN notices (Red only; structure should not preclude adding them later).
- Mobile app.

---

## 3. Data source (Interpol public web service)

Base URL: `https://ws-public.interpol.int`. No API key required.

| Endpoint | Purpose |
|---|---|
| `GET /notices/v1/red` | List notices; filterable + paginated |
| `GET /notices/v1/red/{noticeID}` | Single notice detail |
| `GET /notices/v1/red/{noticeID}/images` | Images for a notice |

List query params: `forename`, `name`, `nationality`, `ageMin`, `ageMax`, `sexId` (`M`/`F`/`U`), `arrestWarrantCountryId`, `page`, `resultPerPage` (up to 200).

**Behavioral constraints to design around (these drive features):**
- The API returns **only currently active notices**. Withdrawn/expired ones silently disappear → this enables withdrawal detection.
- `thumbnailUrl` may be **null** (not every notice has a photo). Handle gracefully.
- The list endpoint **caps total results** (you cannot page through the entire dataset with one unfiltered query). To get broad coverage, the fetcher must **sweep a configurable filter space** (e.g. iterate over a list of nationalities / age buckets) rather than assuming a single paginated crawl returns everything.
- `noticeID` format is like `2021/12345` (contains a slash) → URL-encode when calling detail/image endpoints.
- Physical-description fields use Interpol codes (e.g. `BRO`, `BLA`). Store raw; optional enrichment is a stretch goal.

---

## 4. Architecture

Event-driven pipeline. Each box is a container in `docker-compose`.

```
                 ┌────────────┐
   schedule ───► │  fetcher   │ ──publish──┐
                 └────────────┘            ▼
                                    ┌──────────────┐
                                    │   rabbitmq   │  (exchange + queue + DLQ)
                                    └──────────────┘
                                           │ consume
                                           ▼
                 ┌────────────┐     ┌──────────────┐     ┌────────────┐
   photos ◄──────│   minio    │◄────│    worker    │────►│ postgres   │
                 └────────────┘     └──────┬───────┘     └────────────┘
                                           │ publish change events
                                           ▼
                                    ┌──────────────┐
                                    │    redis     │  (pub/sub + dedup cache)
                                    └──────┬───────┘
                                           │ subscribe
                                           ▼
                 ┌────────────┐     ┌──────────────┐
   browser ◄────►│    api     │◄────│  websocket   │  (live UI updates)
                 └────────────┘     └──────────────┘
```

### Services
1. **fetcher** — scheduled poller. Sweeps the Interpol filter space, paginates, publishes one message per notice to RabbitMQ. Emits a per-cycle "manifest" marker so the worker can reconcile withdrawals. Retry + exponential backoff on HTTP failures.
2. **rabbitmq** — message broker with a main queue and a dead-letter queue for poison messages.
3. **worker** — consumes notices: fetches detail + images, uploads photos to MinIO, runs change-detection against Postgres, writes current state + history, publishes change events to Redis. Idempotent.
4. **postgres** — durable store. Current state + full version history. Alembic-managed schema.
5. **minio** — S3-compatible object storage for notice photos. DB stores object keys only; UI gets presigned URLs.
6. **redis** — pub/sub channel for fanning change events out to (possibly multiple) API instances over WebSocket; also a short-TTL dedup/seen cache.
7. **api** — FastAPI app: REST endpoints + server-rendered UI (Jinja + HTMX) + WebSocket endpoint that streams the live alerts feed.
8. **(stretch) prometheus + grafana** — metrics & dashboards.

---

## 5. Tech stack (pinned)

- Python **3.12**
- **FastAPI** + Uvicorn (api)
- **Jinja2 + HTMX + WebSocket** (UI; no separate JS framework)
- **SQLAlchemy 2.0** ORM — async (`asyncpg`) in the api, sync session in the worker/fetcher
- **Alembic** (migrations)
- **RabbitMQ** + `pika` (fetcher/worker are sync, so plain pika is fine)
- **MinIO** Python SDK (`minio`)
- **Redis** + `redis-py`
- **pydantic-settings** (config)
- **structlog** (structured JSON logging, correlation IDs)
- **pytest** + `pytest-asyncio` + **testcontainers** + coverage
- **ruff** + **mypy** + **pre-commit**
- **Docker** (multi-stage, non-root) + **docker-compose** + **Makefile**
- **GitHub Actions** CI

---

## 6. Data model (PostgreSQL)

### `notices` — current state
- `notice_id` (PK, text — e.g. `2021/12345`)
- `forename`, `name` (text)
- `sex_id` (text, nullable)
- `date_of_birth` (text/date, nullable)
- `nationalities` (text[] / jsonb)
- `arrest_warrant_countries` (jsonb)
- `charge_text` (text, nullable)
- `thumbnail_object_key` (text, nullable — MinIO key; null when no photo)
- `content_hash` (text — hash of normalized payload, drives change detection)
- `status` (enum: `active`, `withdrawn`)
- `raw_json` (jsonb — full normalized source payload)
- `first_seen_at`, `last_seen_at`, `last_changed_at` (timestamptz)

Indexes: `status`, `last_changed_at`, GIN on `nationalities`.

### `notice_history` — audit / SCD type-2
- `id` (PK, bigint identity)
- `notice_id` (FK → notices)
- `version` (int, incrementing per notice)
- `change_type` (enum: `created`, `updated`, `withdrawn`)
- `content_hash` (text)
- `snapshot` (jsonb — full state at this version)
- `diff` (jsonb, nullable — field-level changes for `updated`)
- `valid_from`, `valid_to` (timestamptz; `valid_to` null = current)
- `recorded_at` (timestamptz)

The **alerts feed** in the UI is simply `notice_history` rows where `change_type IN ('updated','withdrawn')`, newest first.

---

## 7. Change-detection logic

On each consumed notice message, the worker:

1. Normalize the payload (drop volatile fields like `_links`, sort keys) and compute `content_hash`.
2. Look up existing row by `notice_id`.
   - **Not found** → INSERT into `notices`; INSERT `notice_history` (`created`, version 1); set `first_seen_at`/`last_seen_at`/`last_changed_at = now`. Publish `created` event (UI may show as new, not as alarm).
   - **Found, hash differs** → compute **field-level diff** (old vs new per field); UPDATE `notices`; close prior history row (`valid_to = now`); INSERT `notice_history` (`updated`, next version, with `diff`); set `last_changed_at = now`. **Publish `updated` alert event with the diff.**
   - **Found, hash identical** → UPDATE `last_seen_at = now` only (idempotent no-op).

### Withdrawal detection
The fetcher emits a per-cycle manifest (the set of `notice_id`s seen this cycle) to a control routing key. After a full cycle the worker marks any `active` notice whose `last_seen_at < cycle_start` as `withdrawn`: UPDATE status, INSERT `notice_history` (`withdrawn`), **publish `withdrawn` alert event.**

This makes the API's "active-only" behavior a feature, not a limitation.

---

## 8. Message queue topology (RabbitMQ)

- Exchange: `notices` (type `topic`).
- Routing keys: `notice.upsert` (per-notice messages), `cycle.complete` (manifest marker).
- Main queue `notices.work` bound to `notice.*`, with DLX → `notices.dead` for messages that exceed retry/`x-max-delivery` or throw repeatedly.
- Worker acks only after successful persistence; nacks (no requeue past N attempts) route to DLQ.
- All exchange/queue names configurable via env.

---

## 9. Object storage (MinIO)

- Bucket name from config (e.g. `interpol-photos`), auto-created on startup if absent.
- Object key scheme: `red/{notice_id_sanitized}/{image_index}.jpg`.
- Worker downloads image(s) from the source, uploads to MinIO, stores the key on the notice. Null source photo → no upload, `thumbnail_object_key = null`.
- API serves photos via **presigned URLs** (configurable expiry), never proxies bytes itself.

---

## 10. Web / API requirements

REST (JSON):
- `GET /api/notices` — paginated, filter by name/nationality/status.
- `GET /api/notices/{id}` — detail incl. presigned photo URL(s).
- `GET /api/alerts` — paginated change feed (updated + withdrawn).
- `GET /healthz`, `GET /readyz` — health/readiness.
- `GET /metrics` — (stretch) Prometheus.

UI (Jinja + HTMX):
- **Dashboard**: latest notices with timestamps + a **live alerts panel**.
- The alerts panel subscribes via **WebSocket**; every `updated`/`withdrawn` event pushes a row in real time, so the interface stays current without polling. Updated/withdrawn entries are visually flagged as **alarms** and show the diff.
- **Detail page**: full fields, photo, and a **version timeline** from `notice_history`.
- Search/filter form (HTMX, no full reloads).

WebSocket: api subscribes to the Redis pub/sub channel and broadcasts to connected clients → works even with multiple api replicas.

---

## 11. Configuration (env-driven, no code changes)

All settings via `pydantic-settings`, loaded from environment / `.env`. Provide `.env.example`. Minimum set:

```
# fetcher
INTERPOL_BASE_URL=https://ws-public.interpol.int
FETCH_INTERVAL_SECONDS=900
FETCH_NATIONALITIES=TR,US,DE,FR,GB        # filter sweep space
FETCH_RESULT_PER_PAGE=200
HTTP_MAX_RETRIES=5
HTTP_BACKOFF_BASE_SECONDS=2

# rabbitmq
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
MQ_EXCHANGE=notices
MQ_WORK_QUEUE=notices.work
MQ_DLQ=notices.dead
MQ_MAX_RETRIES=5

# postgres
POSTGRES_DSN=postgresql+asyncpg://app:app@postgres:5432/interpol
POSTGRES_SYNC_DSN=postgresql+psycopg://app:app@postgres:5432/interpol

# minio
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_BUCKET=interpol-photos
MINIO_SECURE=false
MINIO_PRESIGN_EXPIRY_SECONDS=3600

# redis
REDIS_URL=redis://redis:6379/0
REDIS_EVENT_CHANNEL=notice-events

# app
LOG_LEVEL=INFO
LOG_FORMAT=json
```

---

## 12. Non-functional requirements

- **OOP** throughout: typed classes for config, repositories, the change-detection engine, the storage/MQ clients (no procedural script dumps).
- **Idempotency**: re-processing the same notice produces no spurious history rows.
- **Resilience**: HTTP retries w/ backoff; DLQ for poison messages; graceful shutdown (finish in-flight ack, close connections).
- **Observability**: structured JSON logs with a correlation id per fetch cycle / message; health & readiness endpoints; (stretch) Prometheus counters for fetched/processed/created/updated/withdrawn/errors.
- **Config**: zero code changes to alter behavior — only env.

---

## 13. Testing requirements

- **Unit**: hashing/normalization, the diff engine, withdrawal logic, config loading. Pure functions, no I/O.
- **Integration (testcontainers)**: spin real Postgres + RabbitMQ + MinIO + Redis; assert end-to-end (publish → consume → DB row + history + MinIO object + Redis event).
- **API**: `httpx`/`TestClient` against endpoints incl. a WebSocket event test.
- **Doctest**: at least one module with runnable docstring examples.
- **Coverage**: target ≥ 80% on core logic.

---

## 14. DevOps / packaging

- **Multi-stage Dockerfile(s)**, non-root user, `.dockerignore`, slim runtime images, per-service `HEALTHCHECK`.
- **docker-compose.yml**: all services, `depends_on` with healthchecks, named volumes (postgres, minio), a shared network. `docker compose up` brings the whole system live.
- **Alembic** migrations run on api/worker startup (or a one-shot migrate service).
- **Makefile**: `make up`, `make test`, `make lint`, `make migrate`, `make fmt`.
- **GitHub Actions** CI: `ruff` → `mypy` → `pytest` (with service containers / testcontainers) → build images. Runs on PR + main.

---

## 15. Repository structure

```
interpol-pipeline/
├── app/
│   ├── common/            # config, db models, schemas, mq client, storage client, logging
│   ├── fetcher/           # scheduled poller + manifest emitter
│   ├── worker/            # consumer + change-detection engine + withdrawal reconciler
│   ├── api/               # FastAPI app, routers, websocket
│   └── web/               # jinja templates + static (htmx)
├── migrations/            # alembic
├── tests/                 # unit + integration + api
├── docker/                # Dockerfiles per service (or shared base)
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── .env.example
├── .github/workflows/ci.yml
└── README.md
```

---

## 16. Build milestones (incremental — drive Claude Code session by session)

- **M1 — Scaffolding**: repo layout, `pyproject.toml`, `common` (config + logging), compose skeleton with healthchecks, all services boot with a `/healthz`.
- **M2 — Ingestion**: fetcher sweeps Interpol + paginates, publishes to RabbitMQ; verify messages land.
- **M3 — Persistence + change detection**: worker consumes, Postgres schema + Alembic, MinIO photo upload, change-detection engine, history table.
- **M4 — Web + live UI**: FastAPI REST, Jinja+HTMX dashboard, detail page with timeline, WebSocket live alerts via Redis.
- **M5 — Resilience**: DLQ, retries/backoff, withdrawal reconciliation, idempotency hardening.
- **M6 — Quality**: tests (unit + testcontainers + api + doctest), ruff/mypy/pre-commit, GitHub Actions CI, README + docs.
- **Stretch**: Prometheus + Grafana; physical-description code enrichment; Yellow/UN notices.

---

## 17. Definition of done

- `docker compose up` brings up all services healthy; the system ingests live Interpol data unattended.
- A real notice change (or a simulated/replayed one) produces an `updated` history row with a correct diff and a live alarm in the UI.
- A notice removed from the active feed is marked `withdrawn` with an alert.
- Photos appear in the UI via MinIO presigned URLs; null-photo notices render cleanly.
- Behavior is changeable purely via `.env`.
- CI is green; coverage ≥ target; README documents architecture, run, and test instructions.
