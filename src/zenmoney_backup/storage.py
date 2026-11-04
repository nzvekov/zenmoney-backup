import logging
import os
import re
from datetime import datetime, tzinfo
from pathlib import Path

import ijson

LOG = logging.getLogger(__name__)

_STAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
_STAMP_PATTERN = r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
_BACKUP_RE = re.compile(rf"^zenmoney_({_STAMP_PATTERN})\.json$")
_TMP_RE = re.compile(rf"^zenmoney_{_STAMP_PATTERN}\.json(\.sha256)?\.tmp$")


class InvalidBackupError(ValueError):
    pass


def validate_json(path: Path) -> None:
    # ijson iterates over JSON events without loading the entire document into RAM.
    # A complete parse also catches trailing garbage, truncation and malformed JSON.
    has_timestamp = False
    try:
        with path.open("rb") as src:
            for prefix, event, value in ijson.parse(src):
                if prefix == "" and event == "map_key" and value == "serverTimestamp":
                    has_timestamp = True
    except (ijson.JSONError, ValueError) as exc:
        raise InvalidBackupError(f"Invalid JSON: {exc}") from exc
    # Without this, an error object sent with HTTP 200 would eventually rotate out the good copies.
    if not has_timestamp:
        raise InvalidBackupError("JSON has no top-level serverTimestamp")


def backup_name(moment: datetime) -> str:
    return f"zenmoney_{moment.strftime(_STAMP_FORMAT)}.json"


def list_backups(directory: Path, timezone: tzinfo) -> list[tuple[datetime, Path]]:
    """Return this project's backups, oldest first, by the time in the file name."""
    backups = []
    for path in directory.iterdir():
        match = _BACKUP_RE.match(path.name)
        if not match or not path.is_file():
            continue
        try:
            created = datetime.strptime(match[1], _STAMP_FORMAT).replace(tzinfo=timezone)
        except ValueError:
            continue
        backups.append((created, path))
    return sorted(backups, key=lambda backup: backup[0])


def _sidecar(target: Path) -> Path:
    return target.with_name(target.name + ".sha256")


def publish(partial: Path, target: Path, sha256: str) -> None:
    # shortcut: a crash between the two replaces leaves a harmless orphan .sha256, nothing removes it.
    sidecar = _sidecar(target)
    sidecar_partial = sidecar.with_name(sidecar.name + ".tmp")
    try:
        with sidecar_partial.open("w") as out:
            out.write(f"{sha256}  {target.name}\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(sidecar_partial, sidecar)
        os.replace(partial, target)
    finally:
        sidecar_partial.unlink(missing_ok=True)
    # Sync the directory entry for durability against sudden power loss.
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rotate(directory: Path, retain: int, timezone: tzinfo) -> int:
    """Delete all but the newest `retain` backups and return how many backups remain."""
    backups = list_backups(directory, timezone)
    removed = 0
    for _, path in backups[:-retain]:
        try:
            # The backup goes last, so after a failure it stays listed and the next rotation retries it.
            _sidecar(path).unlink(missing_ok=True)
            path.unlink()
        except OSError as exc:
            LOG.error("Rotation: could not remove %s: %s", path.name, exc)
        else:
            removed += 1
            LOG.info("Rotation: removed %s", path.name)
    remaining = len(backups) - removed
    LOG.info("Rotation finished: removed=%d remaining=%d retain=%d", removed, remaining, retain)
    return remaining


def sweep_partials(directory: Path) -> None:
    """Remove files left by an interrupted run. Call only while holding the backup lock."""
    for path in directory.iterdir():
        if _TMP_RE.match(path.name):
            try:
                path.unlink()
                LOG.warning("Removed leftover temporary file %s", path.name)
            except OSError as exc:
                LOG.error("Could not remove temporary file %s: %s", path.name, exc)
