from __future__ import annotations

try:
    from curl_cffi.requests import (
        Session as CffiSession,  # type: ignore[import-untyped,unused-ignore]
    )
except ImportError:
    CffiSession = None  # type: ignore[assignment,misc]

from app.common.logging import get_logger
from app.common.storage import StorageClient

log = get_logger(__name__)


def _object_key(notice_id: str, index: int = 0) -> str:
    """Deterministic MinIO key for a notice photo.

    Notice IDs contain '/' (e.g. "2021/12345"); replace with '_' so the key is
    a single path component rather than a nested directory.

    >>> _object_key("2021/12345")
    'red/2021_12345/0.jpg'
    """
    return f"red/{notice_id.replace('/', '_')}/{index}.jpg"


class PhotoService:
    """Download a notice thumbnail and upload it to MinIO.

    Uses curl_cffi (Chrome TLS impersonation) because the Interpol image
    endpoint is served behind the same Akamai gate as the API.
    """

    def __init__(self, storage: StorageClient, settings: object) -> None:
        self._storage = storage
        self._settings = settings

    def process(self, notice_id: str, thumbnail_url: str | None) -> str | None:
        """Download *thumbnail_url* and upload to MinIO.

        Returns the object key on success, None if the URL is absent or any
        step fails (download error, upload error).  Failures are logged as
        warnings — they must not abort notice processing.
        """
        if not thumbnail_url:
            return None

        data = self._download(thumbnail_url)
        if data is None:
            return None

        key = _object_key(notice_id)
        try:
            self._storage.upload(key, data)
            log.info("photo.uploaded", notice_id=notice_id, key=key, bytes=len(data))
            return key
        except Exception as exc:
            log.warning("photo.upload_error", notice_id=notice_id, key=key, error=str(exc))
            return None

    def _download(self, url: str) -> bytes | None:
        if CffiSession is None:
            log.warning("photo.curl_cffi_unavailable", url=url)
            return None
        try:
            with CffiSession(
                impersonate=getattr(self._settings, "INTERPOL_IMPERSONATE", "chrome120"),
                headers={"Referer": getattr(self._settings, "INTERPOL_REFERER", "https://www.interpol.int/")},
            ) as session:
                resp = session.get(url, timeout=30)
                if resp.status_code != 200:
                    log.warning("photo.download_failed", status=resp.status_code, url=url)
                    return None
                return bytes(resp.content)
        except Exception as exc:
            log.warning("photo.download_error", url=url, error=str(exc))
            return None
