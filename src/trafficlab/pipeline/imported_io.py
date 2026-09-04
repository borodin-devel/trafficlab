# pyright: reportUnusedFunction=false
"""Deadline-aware stable file reads for imported reference acquisition."""

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

from trafficlab.artifacts.io import FileIdentity
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError

_READ_CHUNK_SIZE = 1024 * 1024
type _PathState = tuple[int, int, int, int, int, int]


def _io_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"imported reference acquisition failed: {detail}",
        corrective_action="restore the original source and run, or select a fresh run.directory",
    )


def _path_state(status: os.stat_result) -> _PathState:
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
        status.st_mode,
    )


def _path_identity(path: Path) -> FileIdentity:
    try:
        status = path.stat(follow_symlinks=False)
    except OSError as error:
        raise _io_error(f"could not inspect source path {path}: {error}") from error
    if not stat.S_ISREG(status.st_mode):
        raise _io_error(f"source path is no longer a regular file: {path}")
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _check_deadline(deadline: float, clock: Callable[[], float]) -> None:
    if clock() >= deadline:
        raise DeadlineExceededError(
            "imported reference acquisition failed: capture.total_timeout_seconds expired",
            corrective_action="increase capture.total_timeout_seconds and retry import-run",
        )


def _check_optional_deadline(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None:
        _check_deadline(deadline, clock)


def _read_bytes_deadline(
    path: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    kind: str,
) -> bytes:
    content = bytearray()
    try:
        initial = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(initial.st_mode):
            raise _io_error(f"{kind} is not a regular file: {path}")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            while True:
                _check_deadline(deadline, clock)
                chunk = stream.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                content.extend(chunk)
            after = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)
    except TrafficlabError:
        raise
    except OSError as error:
        raise _io_error(f"could not read {kind} {path}: {error}") from error
    if _path_state(initial) != _path_state(before) or _path_state(before) != _path_state(after):
        raise _io_error(f"{kind} changed while being read: {path}")
    if _path_state(after) != _path_state(current) or len(content) != current.st_size:
        raise _io_error(f"{kind} changed while being read: {path}")
    return bytes(content)


def _identify_file_deadline(
    path: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    kind: str,
) -> ContentIdentity:
    digest = hashlib.sha256()
    byte_count = 0
    try:
        initial = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(initial.st_mode):
            raise _io_error(f"{kind} is not a regular file: {path}")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            while True:
                _check_deadline(deadline, clock)
                chunk = stream.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                byte_count += len(chunk)
            after = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)
    except TrafficlabError:
        raise
    except OSError as error:
        raise _io_error(f"could not identify {kind} {path}: {error}") from error
    if _path_state(initial) != _path_state(before) or _path_state(before) != _path_state(after):
        raise _io_error(f"{kind} changed while its content identity was computed: {path}")
    if _path_state(after) != _path_state(current) or byte_count != current.st_size:
        raise _io_error(f"{kind} changed while its content identity was computed: {path}")
    return ContentIdentity(size=byte_count, sha256=digest.hexdigest())
