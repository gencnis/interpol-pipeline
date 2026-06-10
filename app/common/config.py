from __future__ import annotations

import functools
from typing import Any, Literal

from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Fields whose env vars are CSV strings rather than JSON arrays.
_CSV_FIELDS: frozenset[str] = frozenset({"FETCH_NATIONALITIES", "FETCH_ARREST_WARRANT_COUNTRIES"})


class _AppEnvSource(EnvSettingsSource):
    """Env source that passes CSV strings through to pydantic validators instead of
    attempting json.loads(), which would reject e.g. FETCH_NATIONALITIES=TR,US,DE."""

    def prepare_field_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        is_csv = field_name in _CSV_FIELDS and isinstance(value, str)
        if is_csv and not value.lstrip().startswith(("[", "{")):
            return value  # let field_validator handle CSV splitting
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Fetcher
    INTERPOL_BASE_URL: str = "https://ws-public.interpol.int"
    INTERPOL_REFERER: str = "https://www.interpol.int/"
    INTERPOL_ORIGIN: str = "https://www.interpol.int"
    INTERPOL_IMPERSONATE: str = "chrome120"
    FETCH_INTERVAL_SECONDS: int = 900
    FETCH_NATIONALITIES: list[str] = ["TR", "US", "DE", "FR", "GB"]
    FETCH_ARREST_WARRANT_COUNTRIES: list[str] = [
        "TR", "US", "DE", "FR", "GB", "RU", "CN", "MX", "BR", "IT"
    ]
    FETCH_RESULT_PER_PAGE: int = 200
    INTERPOL_CAP: int = 160
    HTTP_MAX_RETRIES: int = 5
    HTTP_BACKOFF_BASE_SECONDS: float = 2.0
    # Withdrawal reconciliation only runs when the cycle yielded at least this
    # many notices.  Tune upward if the default filter space reliably returns
    # more; lower for narrow test setups.
    WITHDRAWAL_MIN_CYCLE_SIZE: int = 50

    # RabbitMQ
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"
    MQ_EXCHANGE: str = "notices"
    MQ_WORK_QUEUE: str = "notices.work"
    MQ_DLQ: str = "notices.dead"
    MQ_MAX_RETRIES: int = 5

    # Postgres
    POSTGRES_DSN: str = "postgresql+asyncpg://app:app@postgres:5432/interpol"
    POSTGRES_SYNC_DSN: str = "postgresql+psycopg://app:app@postgres:5432/interpol"

    # MinIO
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "interpol-photos"
    MINIO_SECURE: bool = False
    MINIO_PRESIGN_EXPIRY_SECONDS: int = 3600

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_EVENT_CHANNEL: str = "notice-events"

    # App
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "pretty"] = "json"

    @field_validator("FETCH_NATIONALITIES", "FETCH_ARREST_WARRANT_COUNTRIES", mode="before")
    @classmethod
    def _parse_csv_list(cls, v: object) -> object:
        if isinstance(v, str):
            return [n.strip() for n in v.split(",") if n.strip()]
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _AppEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
