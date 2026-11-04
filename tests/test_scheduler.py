import os
import signal
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import TOKEN

from zenmoney_backup.scheduler import is_backup_missed, last_scheduled_time

TZ = ZoneInfo("Asia/Tbilisi")


def at(day, hour, minute=0, second=0):
    return datetime(2026, 1, day, hour, minute, second, tzinfo=TZ)


def write_backup(cfg, moment):
    cfg.directory.mkdir(exist_ok=True)
    (cfg.directory / f"zenmoney_{moment:%Y-%m-%d_%H-%M-%S}.json").write_text("{}")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(10, 2, 59, 59), at(9, 3)),
        (at(10, 3), at(10, 3)),
        (at(10, 3, 0, 1), at(10, 3)),
        (at(10, 23), at(10, 3)),
        (at(1, 1), datetime(2025, 12, 31, 3, tzinfo=TZ)),
    ],
)
def test_last_scheduled_time(now, expected):
    assert last_scheduled_time(now, 3, 0) == expected


def test_first_start_counts_as_missed(cfg):
    assert is_backup_missed(cfg, at(10, 12)) is True


def test_empty_directory_counts_as_missed(cfg):
    cfg.directory.mkdir()
    assert is_backup_missed(cfg, at(10, 12)) is True


def test_unreadable_directory_counts_as_missed(cfg, monkeypatch):
    def denied(*args):
        raise PermissionError("denied")

    monkeypatch.setattr("zenmoney_backup.scheduler.list_backups", denied)

    assert is_backup_missed(cfg, at(10, 12)) is True


@pytest.mark.parametrize(
    ("latest", "now", "missed"),
    [
        (at(9, 3), at(10, 2), False),  # next run is still ahead
        (at(9, 3), at(10, 3), True),  # the run due right now has not happened
        (at(9, 3), at(10, 12), True),  # today's run was missed
        (at(10, 3), at(10, 12), False),  # today's run happened
        (at(8, 3), at(10, 2), True),  # yesterday's run was missed
        (at(10, 2), at(10, 12), True),  # manual copy before the schedule; run_backup then skips it
    ],
)
def test_missed_backup_detection(cfg, latest, now, missed):
    write_backup(cfg, latest)
    assert is_backup_missed(cfg, now) is missed


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_scheduler_process_stops_on_signal(tmp_path, sig):
    env = {
        **os.environ,
        "API_TOKEN": TOKEN,
        "BACKUP_DIR": str(tmp_path),
        "CATCH_UP_MISSED": "false",
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "zenmoney_backup"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        for line in process.stdout:
            if "Scheduled daily backup" in line:
                break
        process.send_signal(sig)
        output, _ = process.communicate(timeout=15)
    finally:
        process.kill()

    assert process.returncode == 0
    assert "Stopped" in output
    assert TOKEN not in output


def test_invalid_config_exits_with_code_2(tmp_path):
    env = {**os.environ, "API_TOKEN": "", "BACKUP_DIR": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "zenmoney_backup", "--once"], env=env, capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 2
    assert "API_TOKEN is required" in result.stdout
