# CLAUDE.md — Interpol Red Notice Pipeline

## What this project is
An event-driven pipeline that ingests Interpol Red Notices, persists them, detects
changes, and serves a live web UI. The full spec lives in **`prd.md`** — treat it as the
source of truth. Build it **milestone by milestone (M1–M6 in the PRD)**; do not jump ahead.

## Working agreement
- Before writing code for a milestone, propose a short plan and wait for my approval.
- One milestone at a time. After each, run the tests and stop for review before the next.
- Keep changes small and commit often with clear messages.
- If anything in `prd.md` is ambiguous, ask — don't guess.

## Stack (pinned — do not substitute without asking)
- Python 3.12
- FastAPI + Uvicorn (api); Jinja2 + HTMX + WebSocket (UI — no separate JS framework)
- SQLAlchemy 2.0 (async/asyncpg in api, sync in worker/fetcher); Alembic
- RabbitMQ + pika
- MinIO SDK; Redis (redis-py)
- pydantic-settings (ALL config via env / `.env` — never hardcode)
- structlog (structured JSON logging)
- pytest + pytest-asyncio + testcontainers; ruff; mypy
- Docker (multi-stage, non-root) + docker-compose; Makefile

## Conventions
- OOP: typed classes for config, repositories, the change-detection engine, MQ/storage
  clients. No procedural script dumps.
- Type hints everywhere; code must pass `ruff` and `mypy`.
- No secrets in code. Everything configurable lives in settings + `.env.example`.
- All inter-service names (queues, buckets, channels) come from config, not literals.

## Commands (keep these working)
- `make up` — docker compose up; all services come up healthy
- `make test` — full test suite
- `make lint` — ruff + mypy
- `make migrate` — alembic upgrade head

## Key domain constraints (from the data source)
- Interpol public API (`ws-public.interpol.int`), no key. **Active notices only** — a notice
  that disappears from the feed is "withdrawn".
- Notice IDs look like `2021/12345` (contains a slash) — URL-encode for detail/image calls.
- `thumbnailUrl` can be **null** — handle missing photos gracefully.
- The list endpoint caps results — **sweep a configurable filter space**; don't assume one
  crawl returns everything.

## Definition of done (per milestone)
Tests pass, lint clean, services healthy under compose, behavior changeable via `.env`, and
the milestone's acceptance criteria in `prd.md` are met.
