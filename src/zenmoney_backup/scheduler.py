import logging
import signal
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from zenmoney_backup.config import Config
from zenmoney_backup.service import run_backup
from zenmoney_backup.storage import list_backups

LOG = logging.getLogger(__name__)

_SHUTDOWN_SIGNALS = {signal.SIGTERM, signal.SIGINT}


def last_scheduled_time(now: datetime, hour: int, minute: int) -> datetime:
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return scheduled if scheduled <= now else scheduled - timedelta(days=1)


def is_backup_missed(cfg: Config, now: datetime) -> bool:
    try:
        backups = list_backups(cfg.directory, cfg.timezone)
    except OSError:
        # A missing folder is created by run_backup, and any other problem is reported by it.
        return True
    return not backups or backups[-1][0] < last_scheduled_time(now, cfg.hour, cfg.minute)


def run_scheduler(cfg: Config) -> int:
    # Signals are blocked before any thread starts and taken with sigwait: this needs no handler,
    # which could deadlock on the Event's lock, and still works when running as PID 1.
    signal.pthread_sigmask(signal.SIG_BLOCK, _SHUTDOWN_SIGNALS)
    stop = threading.Event()
    scheduler = BackgroundScheduler(timezone=cfg.timezone)
    scheduler.add_job(
        run_backup,
        "cron",
        hour=cfg.hour,
        minute=cfg.minute,
        args=[cfg],
        kwargs={"stop": stop},
        id="daily_backup",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    if cfg.catch_up and is_backup_missed(cfg, datetime.now(cfg.timezone)):
        LOG.warning("Scheduled backup was missed, running it now")
        scheduler.add_job(run_backup, args=[cfg], kwargs={"stop": stop}, id="catch_up", misfire_grace_time=None)
    scheduler.start()
    LOG.info(
        "Scheduled daily backup at %02d:%02d timezone=%s retention=%d", cfg.hour, cfg.minute, cfg.timezone, cfg.retain
    )

    received = signal.sigwait(_SHUTDOWN_SIGNALS)
    LOG.info("Received %s, shutting down", signal.Signals(received).name)
    stop.set()
    scheduler.shutdown(wait=True)
    LOG.info("Stopped")
    return 0
