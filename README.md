# Interpol Red Notice Pipeline

Event-driven pipeline that continuously ingests Interpol Red Notices, detects changes,
and surfaces a live web UI with real-time alerts.

## Quick start

```bash
cp .env.example .env   # edit credentials if needed
make up                # docker compose up --build
```

All 7 services come up healthy. The API is available at `http://localhost:8000`.

## Development

```bash
pip install -e ".[dev]"
make lint     # ruff check + mypy
make test     # pytest
make migrate  # alembic upgrade head (requires running postgres)
make fmt      # ruff format + autofix
```

## Architecture

```
fetcher → rabbitmq → worker → postgres + minio + redis ← api ← browser
```

See `prd.md` for the full specification and milestone plan.
