import logging
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

from zenmoney_backup.config import DIFF_URL, Config

VALID_BODY = b'{"serverTimestamp": 1700000000, "transaction": []}'
TOKEN = "secret-test-token"


@pytest.fixture
def cfg(tmp_path):
    return Config(
        token=TOKEN,
        directory=tmp_path / "backups",
        url=DIFF_URL,
        timeout=120,
        retries=2,
        retry_delay=0,
        max_bytes=2 * 1024**3,
        retain=3,
        timezone=ZoneInfo("Asia/Tbilisi"),
        hour=3,
        minute=0,
        catch_up=True,
        log_level=logging.INFO,
    )


@pytest.fixture
def make_cfg(cfg):
    def make(**overrides):
        return replace(cfg, **overrides)

    return make
