import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import VALID_BODY

from zenmoney_backup.storage import (
    InvalidBackupError,
    backup_name,
    list_backups,
    publish,
    rotate,
    sweep_partials,
    validate_json,
)

TZ = ZoneInfo("Asia/Tbilisi")


def make_backup(directory, day):
    path = directory / f"zenmoney_2026-01-{day:02d}_03-00-00.json"
    path.write_bytes(VALID_BODY)
    path.with_name(path.name + ".sha256").write_text("hash\n")
    return path


def test_valid_json_passes(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(VALID_BODY)
    validate_json(path)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"   ",
        b"not json",
        b'{"serverTimestamp": 1, "tag": [',
        b'{"serverTimestamp": 1} trailing',
        b'{"serverTimestamp": 1}{"serverTimestamp": 2}',
        b'{"error": "oops"}',
        b"{}",
        b"[]",
        b'[{"serverTimestamp": 1}]',
        b'{"nested": {"serverTimestamp": 1}}',
        b'\xff\xfe{"serverTimestamp": 1}',
    ],
)
def test_invalid_json_is_rejected(tmp_path, content):
    path = tmp_path / "file"
    path.write_bytes(content)
    with pytest.raises(InvalidBackupError):
        validate_json(path)


def test_backup_name_format():
    moment = datetime(2026, 3, 5, 7, 8, 9, tzinfo=TZ)
    assert backup_name(moment) == "zenmoney_2026-03-05_07-08-09.json"


def test_publish_moves_file_and_writes_matching_checksum(tmp_path):
    partial = tmp_path / "zenmoney_2026-01-01_03-00-00.json.tmp"
    partial.write_bytes(VALID_BODY)
    target = tmp_path / "zenmoney_2026-01-01_03-00-00.json"
    sha256 = hashlib.sha256(VALID_BODY).hexdigest()

    publish(partial, target, sha256)

    assert target.read_bytes() == VALID_BODY
    assert not partial.exists()
    sidecar = tmp_path / "zenmoney_2026-01-01_03-00-00.json.sha256"
    assert sidecar.read_text() == f"{sha256}  {target.name}\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([target.name, sidecar.name])


def test_list_backups_sorts_by_name_time_and_ignores_foreign_files(tmp_path):
    newest = make_backup(tmp_path, 20)
    oldest = make_backup(tmp_path, 2)
    for foreign in [
        "notes.json",
        "zenmoney_latest.json",
        "zenmoney_2026-13-45_99-00-00.json",
        "zenmoney_2026-01-01_03-00-00.json.tmp",
        "zenmoney_2026-01-01_03-00-00_copy.json",
    ]:
        (tmp_path / foreign).write_text("x")
    (tmp_path / "zenmoney_2026-01-03_03-00-00.json").mkdir()

    assert [path for _, path in list_backups(tmp_path, TZ)] == [oldest, newest]


def test_rotate_keeps_newest_and_removes_checksums(tmp_path):
    backups = [make_backup(tmp_path, day) for day in range(1, 6)]

    assert rotate(tmp_path, 2, TZ) == 2

    for path in backups[:3]:
        assert not path.exists()
        assert not path.with_name(path.name + ".sha256").exists()
    for path in backups[3:]:
        assert path.exists()
        assert path.with_name(path.name + ".sha256").exists()


def test_rotate_with_fewer_backups_than_limit_removes_nothing(tmp_path):
    backups = [make_backup(tmp_path, day) for day in (1, 15)]

    assert rotate(tmp_path, 30, TZ) == 2

    assert all(path.exists() for path in backups)


def test_rotate_does_not_touch_foreign_files(tmp_path):
    make_backup(tmp_path, 1)
    make_backup(tmp_path, 2)
    foreign = [tmp_path / "notes.json", tmp_path / "zenmoney_latest.json", tmp_path / "other.sha256"]
    for path in foreign:
        path.write_text("x")

    rotate(tmp_path, 1, TZ)

    assert all(path.exists() for path in foreign)


def test_rotate_survives_delete_error_and_retries_next_time(tmp_path, monkeypatch):
    first, second, third = (make_backup(tmp_path, day) for day in (1, 2, 3))
    real_unlink = Path.unlink

    def failing_unlink(self, missing_ok=False):
        if self == first:
            raise PermissionError("denied")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    assert rotate(tmp_path, 1, TZ) == 2
    assert first.exists()
    assert not second.exists()
    assert third.exists()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert rotate(tmp_path, 1, TZ) == 1
    assert not first.exists()


def test_sweep_removes_only_own_temporary_files(tmp_path):
    own = [
        tmp_path / "zenmoney_2026-01-01_03-00-00.json.tmp",
        tmp_path / "zenmoney_2026-01-01_03-00-00.json.sha256.tmp",
    ]
    foreign = [tmp_path / "other.tmp", tmp_path / "zenmoney_2026-01-01_03-00-00.json"]
    for path in own + foreign:
        path.write_text("x")

    sweep_partials(tmp_path)

    assert not any(path.exists() for path in own)
    assert all(path.exists() for path in foreign)
