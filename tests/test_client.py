import hashlib
import json
import logging
import threading
import tracemalloc

import httpx
import pytest
from conftest import TOKEN, VALID_BODY

from zenmoney_backup.client import BackupCancelledError, BackupError, fetch_backup, retry_delay


@pytest.fixture
def partial(cfg):
    cfg.directory.mkdir()
    return cfg.directory / "partial.tmp"


def responses(*items):
    """Mock transport that answers with `items` in order and records the requests."""
    queue = list(items)
    requests = []

    def handler(request):
        requests.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    transport = httpx.MockTransport(handler)
    transport.requests = requests
    return transport


def test_sends_full_diff_request(cfg, partial):
    transport = responses(httpx.Response(200, content=VALID_BODY))

    download = fetch_backup(cfg, partial, threading.Event(), transport)

    request = transport.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.zenmoney.ru/v8/diff/"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert body["serverTimestamp"] == 0
    assert body["currentClientTimestamp"] > 0
    assert partial.read_bytes() == VALID_BODY
    assert download.size == len(VALID_BODY)
    assert download.sha256 == hashlib.sha256(VALID_BODY).hexdigest()


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_status_is_not_retried(cfg, partial, status):
    transport = responses(httpx.Response(status))

    with pytest.raises(BackupError, match=str(status)):
        fetch_backup(cfg, partial, threading.Event(), transport)

    assert len(transport.requests) == 1


def test_redirect_is_not_followed(cfg, partial):
    transport = responses(httpx.Response(302, headers={"Location": "https://example.com/"}))

    with pytest.raises(BackupError, match="302"):
        fetch_backup(cfg, partial, threading.Event(), transport)

    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(429),
        httpx.Response(500),
        httpx.Response(502),
        httpx.Response(503),
        httpx.Response(504),
        httpx.ConnectError("unavailable"),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, content=b'{"serverTimestamp": 1, "tra'),
    ],
)
def test_temporary_failure_is_retried(cfg, partial, failure):
    transport = responses(failure, httpx.Response(200, content=VALID_BODY))

    fetch_backup(cfg, partial, threading.Event(), transport)

    assert len(transport.requests) == 2
    assert partial.read_bytes() == VALID_BODY


def test_connection_lost_during_download_is_retried(cfg, partial):
    def broken_stream():
        yield b'{"serverTimestamp": 1, "transaction": ['
        raise httpx.ReadError("connection lost")

    transport = responses(httpx.Response(200, content=broken_stream()), httpx.Response(200, content=VALID_BODY))

    fetch_backup(cfg, partial, threading.Event(), transport)

    assert partial.read_bytes() == VALID_BODY


def test_gives_up_after_configured_retries(cfg, partial):
    transport = responses(*[httpx.Response(503) for _ in range(cfg.retries + 1)])

    with pytest.raises(BackupError, match="HTTP 503"):
        fetch_backup(cfg, partial, threading.Event(), transport)

    assert len(transport.requests) == cfg.retries + 1


@pytest.mark.parametrize(
    ("header", "expected"),
    [("120", 120), ("1", 30), ("100000", 600), ("Wed, 21 Oct 2026 07:28:00 GMT", 30), (None, 30)],
)
def test_retry_delay_respects_retry_after(make_cfg, partial, header, expected):
    cfg = make_cfg(retry_delay=30)
    headers = {"Retry-After": header} if header else {}
    transport = responses(httpx.Response(429, headers=headers), httpx.Response(200, content=VALID_BODY))
    captured = []

    class Stop(threading.Event):
        def wait(self, timeout=None):
            captured.append(timeout)
            return False

    fetch_backup(cfg, partial, Stop(), transport)

    assert captured == [expected]


def test_retry_delay_for_network_error_is_configured_value(make_cfg):
    assert retry_delay(make_cfg(retry_delay=7), httpx.ConnectError("down")) == 7


def test_stop_during_retry_wait_cancels(make_cfg, partial):
    stop = threading.Event()
    stop.set()
    transport = responses(httpx.Response(503))

    with pytest.raises(BackupCancelledError):
        fetch_backup(make_cfg(retry_delay=60), partial, stop, transport)


def test_stop_during_download_cancels(cfg, partial):
    stop = threading.Event()

    def stream():
        yield b"x" * 2_000_000
        stop.set()
        yield b"x" * 2_000_000

    with pytest.raises(BackupCancelledError):
        fetch_backup(cfg, partial, stop, responses(httpx.Response(200, content=stream())))


def test_size_limit_is_enforced_without_retry(make_cfg, partial):
    transport = responses(httpx.Response(200, content=VALID_BODY))

    with pytest.raises(BackupError, match="MAX_BACKUP_BYTES"):
        fetch_backup(make_cfg(max_bytes=5), partial, threading.Event(), transport)

    assert len(transport.requests) == 1


def test_large_response_is_not_loaded_into_memory(cfg, partial):
    item = b'"' + b"x" * (1024 * 1024) + b'"'
    count = 50

    def stream():
        yield b'{"serverTimestamp": 1, "transaction": ['
        for index in range(count):
            yield item if index == 0 else b"," + item
        yield b"]}"

    tracemalloc.start()
    try:
        download = fetch_backup(cfg, partial, threading.Event(), responses(httpx.Response(200, content=stream())))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert download.size > count * 1024 * 1024
    assert partial.stat().st_size == download.size
    assert peak < 10 * 1024 * 1024


def test_token_is_not_logged(cfg, partial, caplog):
    caplog.set_level(logging.DEBUG)
    transport = responses(httpx.ConnectError("down"), httpx.Response(503), httpx.Response(401))

    with pytest.raises(BackupError):
        fetch_backup(cfg, partial, threading.Event(), transport)

    assert TOKEN not in caplog.text
    assert TOKEN not in repr(cfg)
