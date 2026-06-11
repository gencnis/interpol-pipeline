FROM python:3.12-slim AS builder
WORKDIR /build
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
COPY pyproject.toml .
COPY app/ ./app/
RUN pip install --no-cache-dir ".[api,common]"

FROM python:3.12-slim AS runtime
RUN useradd --system --uid 1001 appuser
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"
WORKDIR /app
COPY app/web/ /app/web/
ENV APP_WEB_PATH=/app/web
COPY migrations/ /migrations/
ENV ALEMBIC_MIGRATIONS_PATH=/migrations
USER appuser
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
