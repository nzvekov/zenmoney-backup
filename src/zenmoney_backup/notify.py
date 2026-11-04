import logging

LOG = logging.getLogger(__name__)


def notify_failure(message: str) -> None:
    """Report a failed backup. Telegram or webhook delivery plugs in here."""
    LOG.error("Backup failed: %s", message)
