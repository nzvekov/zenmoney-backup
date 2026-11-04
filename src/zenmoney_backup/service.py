import errno
import fcntl
import logging
import threading
import time
from datetime import datetime

import httpx

from zenmoney_backup.client import BackupCancelledError, BackupError, fetch_backup
from zenmoney_backup.config import Config
from zenmoney_backup.notify import notify_failure
from zenmoney_backup.storage import backup_name, list_backups, publish, rotate, sweep_partials

LOG = logging.getLogger(__name__)

_LOCK_NAME = ".backup.lock"


def _has_backup_for_today(cfg: Config, now: datetime) -> bool:
    return any(created.date() == now.date() for created, _ in list_backups(cfg.directory, cfg.timezone))


def _run_locked(cfg: Config, force: bool, stop: threading.Event, transport: httpx.BaseTransport | None) -> bool:
    now = datetime.now(cfg.timezone)
    sweep_partials(cfg.directory)
    if not force and _has_backup_for_today(cfg, now):
        LOG.info("A backup for %s already exists, skipping", now.date())
        return True

    LOG.info("Backup started")
    target = cfg.directory / backup_name(now)
    partial = target.with_name(target.name + ".tmp")
    try:
        download = fetch_backup(cfg, partial, stop, transport)
        publish(partial, target, download.sha256)
    finally:
        partial.unlink(missing_ok=True)
    LOG.info("Backup saved path=%s bytes=%d sha256=%s", target, download.size, download.sha256)

    # The new copy is already safe, so a rotation problem must not fail the backup.
    try:
        rotate(cfg.directory, cfg.retain, cfg.timezone)
    except OSError as exc:
        LOG.error("Rotation failed: %s", exc)
    return True


def run_backup(
    cfg: Config,
    *,
    force: bool = False,
    stop: threading.Event | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """Run one backup. Existing copies stay untouched unless the new one is saved successfully."""
    started = time.monotonic()
    try:
        cfg.directory.mkdir(parents=True, exist_ok=True)
        with (cfg.directory / _LOCK_NAME).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                LOG.warning("Another backup is running, skipping")
                return False
            return _run_locked(cfg, force, stop or threading.Event(), transport)
    except BackupCancelledError:
        LOG.warning("Backup cancelled by shutdown")
    except BackupError as exc:
        notify_failure(str(exc))
    except OSError as exc:
        reason = "not enough disk space" if exc.errno == errno.ENOSPC else "disk error"
        notify_failure(f"{reason}: {exc}")
    except Exception as exc:
        LOG.exception("Unexpected error")
        notify_failure(f"unexpected error: {type(exc).__name__}")
    finally:
        LOG.info("Job finished total_time=%.1fs", time.monotonic() - started)
    return False
