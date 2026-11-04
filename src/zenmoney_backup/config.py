import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

DIFF_URL = "https://api.zenmoney.ru/v8/diff/"


class ConfigError(ValueError):
    pass


def integer_env(key: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.getenv(key) or str(default)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer") from None
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigError(f"{key} is out of range")
    return value


def _boolean_env(key: str, default: bool) -> bool:
    raw = (os.getenv(key) or str(default)).strip().lower()
    if raw not in ("true", "false"):
        raise ConfigError(f"{key} must be true or false")
    return raw == "true"


def _token_env() -> str:
    token = os.getenv("API_TOKEN", "").strip()
    if not token:
        raise ConfigError("API_TOKEN is required")
    # An illegal header value would end up, token included, in the HTTP client's error message.
    if not token.isascii() or not token.isprintable() or " " in token:
        raise ConfigError("API_TOKEN contains illegal characters")
    return token


def _timezone_env() -> ZoneInfo:
    name = os.getenv("TZ") or "Asia/Tbilisi"
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        raise ConfigError(f"Unknown timezone in TZ: {name}") from None


def _log_level_env() -> int:
    name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    levels = logging.getLevelNamesMapping()
    if name not in levels:
        raise ConfigError(f"Unknown LOG_LEVEL: {name}")
    return levels[name]


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    directory: Path
    url: str
    timeout: int
    retries: int
    retry_delay: int
    max_bytes: int
    retain: int
    timezone: ZoneInfo
    hour: int
    minute: int
    catch_up: bool
    log_level: int

    @classmethod
    def from_env(cls) -> "Config":
        url = os.getenv("API_URL") or DIFF_URL
        if not url.startswith("https://"):
            raise ConfigError("API_URL must use HTTPS")
        return cls(
            token=_token_env(),
            directory=Path(os.getenv("BACKUP_DIR") or "/backups"),
            url=url,
            timeout=integer_env("API_TIMEOUT", 120, 1),
            retries=integer_env("API_RETRY_COUNT", 3),
            retry_delay=integer_env("API_RETRY_DELAY", 30),
            max_bytes=integer_env("MAX_BACKUP_BYTES", 2 * 1024**3, 1),
            retain=integer_env("RETENTION_COUNT", 30, 1),
            timezone=_timezone_env(),
            hour=integer_env("BACKUP_HOUR", 3, 0, 23),
            minute=integer_env("BACKUP_MINUTE", 0, 0, 59),
            catch_up=_boolean_env("CATCH_UP_MISSED", True),
            log_level=_log_level_env(),
        )
