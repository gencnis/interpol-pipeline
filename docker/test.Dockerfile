FROM python:3.12-slim
WORKDIR /src
COPY pyproject.toml .
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY tests/ ./tests/
COPY alembic.ini .
ENV ALEMBIC_MIGRATIONS_PATH=/src/migrations
RUN pip install --no-cache-dir ".[dev]"
CMD ["pytest", "-v"]
