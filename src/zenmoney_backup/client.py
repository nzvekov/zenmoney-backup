import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from zenmoney_backup.config import Config
from zenmoney_backup.storage import InvalidBackupError, validate_json

LOG = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_MAX_RETRY_AFTER = 600


class BackupError(Exception):
    pass


class BackupCancelledError(Exception):
    pass


class _RetryableStatusError(Exception):
    def __init__(self, status: int, retry_after: str | None):
        super().__init__(f"HTTP {status}")
        self.retry_after = retry_after


@dataclass(frozen=True)
class Download:
    size: int
    sha256: str


def retry_delay(cfg: Config, error: Exception) -> int:
    header = error.retry_after if isinstance(error, _RetryableStatusError) else None
    # Retry-After may also be an HTTP date; the configured delay is good enough for that case.
    requested = int(header) if header and header.isdecimal() else 0
    return max(cfg.retry_delay, min(requested, _MAX_RETRY_AFTER))


def _download_once(cfg: Config, client: httpx.Client, partial: Path, stop: threading.Event) -> Download:
    started = time.monotonic()
    # serverTimestamp=0 asks for a full copy instead of changes since the last sync.
    body = {"serverTimestamp": 0, "currentClientTimestamp": int(time.time())}
    with client.stream("POST", cfg.url, json=body) as response:
        status = response.status_code
        LOG.info("HTTP status=%d response_time=%.1fs", status, time.monotonic() - started)
        if status == 429 or status >= 500:
            raise _RetryableStatusError(status, response.headers.get("Retry-After"))
        if status in (401, 403):
            raise BackupError(f"HTTP {status}: authorization failed, check API_TOKEN")
        if status != 200:
            raise BackupError(f"HTTP {status}: unexpected response status")

        digest = hashlib.sha256()
        size = 0
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(_CHUNK_SIZE):
                if stop.is_set():
                    raise BackupCancelledError
                size += len(chunk)
                if size > cfg.max_bytes:
                    raise BackupError("Response exceeds MAX_BACKUP_BYTES")
                out.write(chunk)
                digest.update(chunk)
            out.flush()
            os.fsync(out.fileno())
    LOG.info("Downloaded bytes=%d download_time=%.1fs", size, time.monotonic() - started)
    return Download(size, digest.hexdigest())


def fetch_backup(
    cfg: Config, partial: Path, stop: threading.Event, transport: httpx.BaseTransport | None = None
) -> Download:
    """Download the full diff into `partial` and validate it, retrying temporary failures."""
    headers = {"Authorization": f"Bearer {cfg.token}"}
    with httpx.Client(headers=headers, timeout=httpx.Timeout(cfg.timeout), transport=transport) as client:
        attempts = cfg.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                download = _download_once(cfg, client, partial, stop)
                validate_json(partial)
                LOG.info("JSON validation passed")
                return download
            except (httpx.RequestError, _RetryableStatusError, InvalidBackupError) as exc:
                LOG.warning("Attempt %d/%d failed: %s: %s", attempt, attempts, type(exc).__name__, exc)
                if attempt == attempts:
                    raise BackupError(f"All {attempts} attempts failed, last error: {exc}") from exc
                delay = retry_delay(cfg, exc)
            LOG.info("Next attempt in %ds", delay)
            if stop.wait(delay):
                raise BackupCancelledError
