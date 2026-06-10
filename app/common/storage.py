from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from typing import Any

from minio import Minio
from minio.error import S3Error

from app.common.logging import get_logger

log = get_logger(__name__)


class StorageClient:
    def __init__(self, settings: Any) -> None:
        self._client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._bucket = settings.MINIO_BUCKET
        self._presign_expiry = settings.MINIO_PRESIGN_EXPIRY_SECONDS

    @classmethod
    def from_params(
        cls,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        presign_expiry: int = 3600,
    ) -> StorageClient:
        """Construct without a Settings object — useful in tests."""
        obj = object.__new__(cls)
        obj._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        obj._bucket = bucket
        obj._presign_expiry = presign_expiry
        return obj

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            log.info("storage.bucket_created", bucket=self._bucket)

    def upload(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        self._client.put_object(
            self._bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def object_exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except S3Error:
            return False

    def get_presigned_url(self, key: str) -> str:
        return self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(seconds=self._presign_expiry)
        )
