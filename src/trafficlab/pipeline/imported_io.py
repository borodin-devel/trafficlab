# pyright: reportUnusedFunction=false
"""Deadline-aware stable file reads for imported reference acquisition."""

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

from trafficlab.artifacts.capture import CapturePublication
from trafficlab.artifacts.io import FileIdentity, file_identity
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError

_READ_CHUNK_SIZE = 1024 * 1024
_COPY_CHUNK_SIZE = 1024 * 1024
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


def _open_regular_readonly(path: Path, *, kind: str, operation: str) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        status = os.fstat(descriptor)
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise _io_error(f"could not {operation} {kind} {path} without following links: {error}") from error
    if not stat.S_ISREG(status.st_mode):
        os.close(descriptor)
        raise _io_error(f"{kind} is not a regular file: {path}")
    return descriptor, status


def _require_unchanged_bound_file(
    path: Path,
    before: os.stat_result,
    after: os.stat_result,
    *,
    byte_count: int,
    kind: str,
    operation: str,
) -> None:
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as error:
        raise _io_error(f"could not revalidate {kind} {path} after {operation}: {error}") from error
    if (
        _path_state(before) != _path_state(after)
        or _path_state(after) != _path_state(current)
        or byte_count != after.st_size
    ):
        raise _io_error(f"{kind} changed while {operation}: {path}")


def _read_bytes_deadline(
    path: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    kind: str,
) -> bytes:
    content = bytearray()
    descriptor, before = _open_regular_readonly(path, kind=kind, operation="read")
    try:
        with os.fdopen(descriptor, "rb") as stream:
            while True:
                _check_deadline(deadline, clock)
                chunk = stream.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                content.extend(chunk)
            after = os.fstat(stream.fileno())
    except TrafficlabError:
        raise
    except OSError as error:
        raise _io_error(f"could not read {kind} {path}: {error}") from error
    _require_unchanged_bound_file(
        path,
        before,
        after,
        byte_count=len(content),
        kind=kind,
        operation="being read",
    )
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
    descriptor, before = _open_regular_readonly(path, kind=kind, operation="identify")
    try:
        with os.fdopen(descriptor, "rb") as stream:
            while True:
                _check_deadline(deadline, clock)
                chunk = stream.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                byte_count += len(chunk)
            after = os.fstat(stream.fileno())
    except TrafficlabError:
        raise
    except OSError as error:
        raise _io_error(f"could not identify {kind} {path}: {error}") from error
    _require_unchanged_bound_file(
        path,
        before,
        after,
        byte_count=byte_count,
        kind=kind,
        operation="its content identity was computed",
    )
    return ContentIdentity(size=byte_count, sha256=digest.hexdigest())


def _copy_snapshot(source: Path, destination: Path, *, deadline: float, clock: Callable[[], float]) -> None:
    byte_count = 0
    descriptor, before = _open_regular_readonly(source, kind="source file", operation="snapshot")
    try:
        with os.fdopen(descriptor, "rb") as input_stream, destination.open("xb") as output_stream:
            while True:
                _check_deadline(deadline, clock)
                chunk = input_stream.read(_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                output_stream.write(chunk)
                byte_count += len(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
            after = os.fstat(input_stream.fileno())
        _check_deadline(deadline, clock)
    except TrafficlabError:
        raise
    except OSError as error:
        raise _io_error(f"could not snapshot source file {source}: {error}") from error
    _require_unchanged_bound_file(
        source,
        before,
        after,
        byte_count=byte_count,
        kind="source file",
        operation="being copied",
    )


def _resolve_publication_warnings(run_directory: Path, publication: CapturePublication) -> None:
    if not publication.warnings:
        return
    owned = publication.owned_identity
    if owned is None:
        raise _io_error("capture publisher reported cleanup warnings without creator ownership")
    try:
        candidates = sorted(
            (path for path in run_directory.iterdir() if path.name.startswith(".capture-pair.")),
            key=lambda path: path.name,
        )
    except OSError as error:
        raise _io_error(f"could not inspect capture publisher cleanup warnings: {error}") from error
    if not candidates:
        raise _io_error(
            f"capture publisher reported cleanup warnings that cannot be safely resolved: {'; '.join(publication.warnings)}"
        )
    identities = [
        file_identity(
            path,
            kind="capture publisher temporary",
            corrective_action="preserve the run and remove only the reported creator-owned temporary",
        )
        for path in candidates
    ]
    if any(identity not in owned for identity in identities):
        raise _io_error("capture publisher cleanup warning refers to a temporary without proven ownership")
    try:
        for path in candidates:
            os.unlink(path)
    except OSError as error:
        raise _io_error(f"capture publisher cleanup retry failed for {path}: {error}") from error
    if any(file_identity(path) is not None for path in candidates):
        raise _io_error("capture publisher cleanup retry left a creator-owned temporary")
