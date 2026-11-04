import argparse
import logging
import signal
import sys

from zenmoney_backup.config import Config, ConfigError
from zenmoney_backup.scheduler import run_scheduler
from zenmoney_backup.service import run_backup

LOG = logging.getLogger(__name__)


def _exit_on_signal(signum, _frame):
    # Unwinds the stack so the temporary file is removed. Without a handler PID 1 would ignore SIGTERM.
    sys.exit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zenmoney-backup", description="Daily full backup of ZenMoney data")
    parser.add_argument("--once", action="store_true", help="make one backup now and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # These loggers must stay quiet even at LOG_LEVEL=DEBUG: request details do not belong in the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        LOG.error("Configuration error: %s", exc)
        return 2
    logging.getLogger().setLevel(cfg.log_level)

    if args.once:
        signal.signal(signal.SIGTERM, _exit_on_signal)
        signal.signal(signal.SIGINT, _exit_on_signal)
        return 0 if run_backup(cfg, force=True) else 1
    return run_scheduler(cfg)
