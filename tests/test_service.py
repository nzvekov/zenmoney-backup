import errno
import fcntl
import hashlib
import threading
from datetime import datetime

import httpx
import pytest
from conftest import VALID_BODY

from zenmoney_backup import service, storage
from zenmoney_backup.storage import backup_name


def ok_transport():
    return httpx.MockTransport(lambda request: httpx.Response(200, content=VALID_BODY))


def failing_transport(status=401):
    return httpx.MockTransport(lambda request: httpx.Response(status))


def make_old_backups(cfg, days):
    cfg.directory.mkdir(exist_ok=True)
    paths = []
    for day in days:
        path = cfg.directory / f"zenmoney_2026-01-{day:02d}_03-00-00.json"
        path.write_bytes(b"old copy")
        path.with_name(path.name + ".sha256").write_text("old hash\n")
        paths.append(path)
    return paths


def snapshot(cfg):
    return {p.name: p.read_bytes() for p in cfg.directory.iterdir() if p.name != ".backup.lock"}


def test_success_saves_backup_and_checksum(cfg):
    assert service.run_backup(cfg, transport=ok_transport()) is True

    expected = backup_name(datetime.now(cfg.timezone))[:19]  # zenmoney_YYYY-MM-DD
    files = snapshot(cfg)
    [name] = [name for name in files if name.endswith(".json")]
    assert name.startswith(expected)
    assert files[name] == VALID_BODY
    assert files[name + ".sha256"].decode() == f"{hashlib.sha256(VALID_BODY).hexdigest()}  {name}\n"
    assert len(files) == 2


def test_success_rotates_old_backups(cfg):
    old = make_old_backups(cfg, range(1, 6))

    assert service.run_backup(cfg, transport=ok_transport()) is True

    assert [path.exists() for path in old] == [False, False, False, True, True]


@pytest.mark.parametrize(
    "transport",
    [
        failing_transport(401),
        failing_transport(503),
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"broken")),
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"")),
    ],
)
def test_failure_keeps_old_backups_and_skips_rotation(cfg, transport):
    make_old_backups(cfg, range(1, 6))
    before = snapshot(cfg)

    assert service.run_backup(cfg, force=True, transport=transport) is False

    assert snapshot(cfg) == before


def test_failure_notifies(cfg, monkeypatch):
    messages = []
    monkeypatch.setattr(service, "notify_failure", messages.append)

    service.run_backup(cfg, transport=failing_transport(401))

    assert len(messages) == 1
    assert "401" in messages[0]


def test_disk_full_is_reported_and_leaves_no_temporary_file(cfg, monkeypatch):
    messages = []
    monkeypatch.setattr(service, "notify_failure", messages.append)
    make_old_backups(cfg, [1])
    before = snapshot(cfg)

    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(service, "publish", no_space)

    assert service.run_backup(cfg, transport=ok_transport()) is False

    assert "not enough disk space" in messages[0]
    assert snapshot(cfg) == before


def test_unusable_directory_fails_cleanly(make_cfg, tmp_path):
    (tmp_path / "file").write_text("x")
    cfg = make_cfg(directory=tmp_path / "file" / "backups")

    assert service.run_backup(cfg, transport=ok_transport()) is False


def test_rotation_failure_does_not_fail_backup(cfg, monkeypatch):
    def broken_rotate(*args):
        raise OSError("listing failed")

    monkeypatch.setattr(service, "rotate", broken_rotate)

    assert service.run_backup(cfg, transport=ok_transport()) is True


def test_busy_lock_skips_run_without_request(cfg):
    cfg.directory.mkdir()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=VALID_BODY)

    with (cfg.directory / ".backup.lock").open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        assert service.run_backup(cfg, force=True, transport=httpx.MockTransport(handler)) is False

    assert calls == []


def test_concurrent_runs_download_once(cfg):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def handler(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, content=VALID_BODY)

    results = []
    first = threading.Thread(
        target=lambda: results.append(service.run_backup(cfg, transport=httpx.MockTransport(handler)))
    )
    first.start()
    assert entered.wait(5)

    assert service.run_backup(cfg, force=True, transport=httpx.MockTransport(handler)) is False

    release.set()
    first.join(5)
    assert results == [True]
    assert len(calls) == 1


def test_one_scheduled_backup_per_day(cfg):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=VALID_BODY)

    transport = httpx.MockTransport(handler)
    assert service.run_backup(cfg, transport=transport) is True
    assert service.run_backup(cfg, transport=transport) is True

    assert len(calls) == 1


def test_force_bypasses_daily_limit(cfg, monkeypatch):
    moments = iter(["zenmoney_2026-01-01_10-00-00.json", "zenmoney_2026-01-01_10-00-01.json"])
    monkeypatch.setattr(service, "backup_name", lambda now: next(moments))

    assert service.run_backup(cfg, force=True, transport=ok_transport()) is True
    assert service.run_backup(cfg, force=True, transport=ok_transport()) is True

    assert len([name for name in snapshot(cfg) if name.endswith(".json")]) == 2


def test_leftovers_of_interrupted_run_are_removed(cfg):
    cfg.directory.mkdir()
    leftover = cfg.directory / "zenmoney_2026-01-01_03-00-00.json.tmp"
    leftover.write_bytes(b'{"serverTimestamp": 1, "tra')

    assert service.run_backup(cfg, transport=ok_transport()) is True

    assert not leftover.exists()


def test_cancelled_run_is_not_reported_as_failure(cfg, monkeypatch):
    messages = []
    monkeypatch.setattr(service, "notify_failure", messages.append)
    stop = threading.Event()
    stop.set()

    assert service.run_backup(cfg, stop=stop, transport=failing_transport(503)) is False

    assert messages == []
    assert not list(cfg.directory.glob("*.tmp"))
    assert storage.list_backups(cfg.directory, cfg.timezone) == []
