"""Pinned Linux filesystem boundaries for Artifact I/O transactions."""

from __future__ import annotations

import errno
import os
import posixpath
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Never

from trafficlab.libs.lineage import DEFAULT_CHUNK_SIZE, MAX_CHUNK_SIZE

from .errors import ArtifactStatusSecurityError, MissingArtifactStatusError
from .values import ARTIFACT_STATUS_NAME, MAX_ARTIFACT_STATUS_BYTES

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_STATUS_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True, slots=True)
class _PinnedEntry:
    parent_fd: int | None
    name: str
    fd: int
    initial: os.stat_result


def _open_fd(path: str, flags: int, *, dir_fd: int | None = None) -> int:
    if dir_fd is None:
        return os.open(path, flags)
    return os.open(path, flags, dir_fd=dir_fd)


def _fstat_fd(descriptor: int) -> os.stat_result:
    return os.fstat(descriptor)


def _stat_entry(path: str, *, dir_fd: int | None = None) -> os.stat_result:
    if dir_fd is None:
        return os.stat(path, follow_symlinks=False)
    return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)


def _read_fd(descriptor: int, count: int) -> bytes:
    return os.read(descriptor, count)


def _close_fd(descriptor: int) -> None:
    os.close(descriptor)


def _effective_uid() -> int:
    return os.geteuid()


def _raise_security(message: str, cause: BaseException) -> Never:
    raise ArtifactStatusSecurityError(message) from cause


def _validate_chunk_size(chunk_size: int) -> None:
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be an integer from 1 through {MAX_CHUNK_SIZE}"
        )


def _validate_attempt_path(attempt_dir: object) -> str:
    if not isinstance(attempt_dir, Path):
        _raise_security(
            "attempt directory path is invalid",
            OSError(errno.EINVAL, "attempt path is not a Path"),
        )
    path = attempt_dir
    text = path.as_posix()
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        _raise_security("attempt directory path is invalid", error)
    if (
        not path.is_absolute()
        or not text
        or text.startswith("//")
        or posixpath.normpath(text) != text
        or any(component in {".", ".."} for component in text.split("/"))
        or (text.endswith("/") and text != "/")
        or text.splitlines() != [text]
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in text)
    ):
        _raise_security(
            "attempt directory path is invalid",
            OSError(errno.EINVAL, "attempt path is not canonical"),
        )
    return text


def _identity_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
    )


def _content_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        *_identity_fingerprint(metadata),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _require_directory(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        _raise_security(
            "attempt directory filesystem envelope is unsafe",
            OSError(errno.ENOTDIR, "attempt component is not a directory"),
        )


def _require_private_attempt(metadata: os.stat_result) -> None:
    _require_directory(metadata)
    if metadata.st_uid != _effective_uid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        _raise_security(
            "attempt directory filesystem envelope is unsafe",
            OSError(errno.EPERM, "attempt owner or mode is unsafe"),
        )


def _require_private_status(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != _effective_uid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        _raise_security(
            "detached status filesystem envelope is unsafe",
            OSError(errno.EPERM, "status owner, mode, type, or links are unsafe"),
        )


def _assert_entry_unchanged(
    entry: _PinnedEntry,
    *,
    compare_content: bool,
) -> None:
    try:
        descriptor_status = _fstat_fd(entry.fd)
        entry_status = _stat_entry(entry.name, dir_fd=entry.parent_fd)
    except OSError as error:
        _raise_security("detached status changed during snapshot", error)
    descriptor_fingerprint = (
        _content_fingerprint(descriptor_status)
        if compare_content
        else _identity_fingerprint(descriptor_status)
    )
    initial_fingerprint = (
        _content_fingerprint(entry.initial)
        if compare_content
        else _identity_fingerprint(entry.initial)
    )
    if descriptor_fingerprint != initial_fingerprint or _identity_fingerprint(
        entry_status
    ) != _identity_fingerprint(entry.initial):
        _raise_security(
            "detached status changed during snapshot",
            OSError(errno.ESTALE, "pinned entry identity changed"),
        )


def _open_attempt_chain(
    attempt_text: str,
    opened: list[int],
) -> list[_PinnedEntry]:
    root_fd = _open_fd("/", _DIRECTORY_OPEN_FLAGS)
    opened.append(root_fd)
    root_status = _fstat_fd(root_fd)
    _require_directory(root_status)
    entries = [_PinnedEntry(None, "/", root_fd, root_status)]
    for component in attempt_text.split("/")[1:]:
        if not component:
            continue
        parent_fd = entries[-1].fd
        descriptor = _open_fd(
            component,
            _DIRECTORY_OPEN_FLAGS,
            dir_fd=parent_fd,
        )
        opened.append(descriptor)
        metadata = _fstat_fd(descriptor)
        _require_directory(metadata)
        entries.append(_PinnedEntry(parent_fd, component, descriptor, metadata))
    _require_private_attempt(entries[-1].initial)
    return entries


def _open_status(
    attempt_fd: int,
    opened: list[int],
) -> _PinnedEntry:
    try:
        descriptor = _open_fd(
            ARTIFACT_STATUS_NAME,
            _STATUS_OPEN_FLAGS,
            dir_fd=attempt_fd,
        )
    except OSError as error:
        if error.errno == errno.ENOENT:
            raise MissingArtifactStatusError(
                "detached artifact status is missing"
            ) from error
        raise
    opened.append(descriptor)
    metadata = _fstat_fd(descriptor)
    entry = _PinnedEntry(attempt_fd, ARTIFACT_STATUS_NAME, descriptor, metadata)
    entry_status = _stat_entry(ARTIFACT_STATUS_NAME, dir_fd=attempt_fd)
    if _identity_fingerprint(entry_status) != _identity_fingerprint(metadata):
        _raise_security(
            "detached status changed during snapshot",
            OSError(errno.ESTALE, "status entry binding changed"),
        )
    _require_private_status(metadata)
    return entry


def _read_bounded_status(status_entry: _PinnedEntry, chunk_size: int) -> bytes:
    retained = bytearray()
    while True:
        request_size = min(
            chunk_size,
            MAX_ARTIFACT_STATUS_BYTES + 1 - len(retained),
        )
        try:
            chunk = _read_fd(status_entry.fd, request_size)
        except OSError as error:
            _raise_security("cannot read detached artifact status", error)
        if len(chunk) > request_size:
            _raise_security(
                "cannot read detached artifact status",
                OSError(errno.EIO, "status read returned an invalid result"),
            )
        if not chunk:
            break
        retained.extend(chunk)
        if len(retained) > MAX_ARTIFACT_STATUS_BYTES:
            break
    return bytes(retained)


def _close_all(opened: list[int]) -> OSError | None:
    first_error: OSError | None = None
    for descriptor in reversed(opened):
        try:
            _close_fd(descriptor)
        except OSError as error:
            if first_error is None:
                first_error = error
    return first_error


def _read_status_snapshot(
    attempt_text: str,
    chunk_size: int,
    opened: list[int],
) -> bytes:
    try:
        attempt_entries = _open_attempt_chain(attempt_text, opened)
        status_entry = _open_status(attempt_entries[-1].fd, opened)
        data = _read_bounded_status(status_entry, chunk_size)
        _assert_entry_unchanged(status_entry, compare_content=True)
        for entry in reversed(attempt_entries):
            _assert_entry_unchanged(entry, compare_content=False)
    except (ArtifactStatusSecurityError, MissingArtifactStatusError):
        raise
    except OSError as error:
        _raise_security("cannot securely snapshot detached status", error)
    if len(data) > MAX_ARTIFACT_STATUS_BYTES:
        _raise_security(
            "detached status exceeds the byte limit",
            OSError(errno.EFBIG, "status exceeds maximum byte size"),
        )
    return data


def _snapshot_status(  # pyright: ignore[reportUnusedFunction]
    attempt_dir: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> bytes:
    """Read one bounded status snapshot through a pinned attempt chain."""

    _validate_chunk_size(chunk_size)
    attempt_text = _validate_attempt_path(attempt_dir)
    opened: list[int] = []
    completed = False
    try:
        data = _read_status_snapshot(attempt_text, chunk_size, opened)
        completed = True
        return data
    finally:
        close_error = _close_all(opened)
        if completed and close_error is not None:
            _raise_security("cannot close detached status snapshot", close_error)
