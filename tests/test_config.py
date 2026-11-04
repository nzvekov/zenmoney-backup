from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import TOKEN

from zenmoney_backup.config import DIFF_URL, Config, ConfigError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (
        "API_URL",
        "API_TIMEOUT",
        "API_RETRY_COUNT",
        "API_RETRY_DELAY",
        "BACKUP_DIR",
        "MAX_BACKUP_BYTES",
        "RETENTION_COUNT",
        "TZ",
        "BACKUP_HOUR",
        "BACKUP_MINUTE",
        "CATCH_UP_MISSED",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("API_TOKEN", TOKEN)


def test_defaults_match_documentation():
    cfg = Config.from_env()

    assert cfg.token == TOKEN
    assert cfg.url == DIFF_URL
    assert cfg.directory == Path("/backups")
    assert (cfg.timeout, cfg.retries, cfg.retry_delay) == (120, 3, 30)
    assert (cfg.retain, cfg.max_bytes) == (30, 2 * 1024**3)
    assert (cfg.hour, cfg.minute) == (3, 0)
    assert cfg.timezone == ZoneInfo("Asia/Tbilisi")
    assert cfg.catch_up is True


def test_empty_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("API_URL", "")
    monkeypatch.setenv("RETENTION_COUNT", "")

    cfg = Config.from_env()

    assert cfg.url == DIFF_URL
    assert cfg.retain == 30


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("API_URL", "http://api.zenmoney.ru/v8/diff/"),
        ("API_TOKEN", ""),
        ("API_TOKEN", "two words"),
        ("API_TOKEN", "line\nbreak"),
        ("API_TOKEN", "токен"),
        ("API_TIMEOUT", "0"),
        ("API_RETRY_COUNT", "-1"),
        ("RETENTION_COUNT", "0"),
        ("RETENTION_COUNT", "many"),
        ("BACKUP_HOUR", "24"),
        ("BACKUP_MINUTE", "60"),
        ("CATCH_UP_MISSED", "maybe"),
        ("TZ", "Mars/Olympus"),
        ("LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_value_is_rejected(monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(ConfigError) as error:
        Config.from_env()

    if key == "API_TOKEN" and value:
        assert value not in str(error.value)
