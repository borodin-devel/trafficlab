"""Bounded exact-byte hashing through stable no-follow file snapshots."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Never

from .errors import (
    FileChangedError,
    FileSnapshotError,
    HashMismatchError,
    InvalidLineagePathError,
    ManifestValidationError,
)
from .values import (
    FileIdentity,
    PathKind,
    Sha256Digest,
    _validate_external_path,  # pyright: ignore[reportPrivateUsage]
    _validate_local_path,  # pyright: ignore[reportPrivateUsage]
)

DEFAULT_CHUNK_SIZE = 65_536
MAX_CHUNK_SIZE = 1_048_576

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LEAF_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True, slots=True)
class _PinnedEntry:
    parent_fd: int
    name: str
    fd: int
    initial: os.stat_result


def _read_chunk(fd: int, size: int) -> bytes:
    return os.read(fd, size)


def _validate_chunk_size(chunk_size: int) -> None:
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be an integer from 1 through {MAX_CHUNK_SIZE}"
        )


def _raise_snapshot_failure(path: str, error: OSError) -> Never:
    raise FileSnapshotError(f"cannot snapshot regular file: {path}") from error


def _open_component(
    parent_fd: int,
    name: str,
    *,
    directory: bool,
    path: str,
) -> _PinnedEntry:
    flags = _DIRECTORY_OPEN_FLAGS if directory else _LEAF_OPEN_FLAGS
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        _raise_snapshot_failure(path, error)

    try:
        initial = os.fstat(descriptor)
    except OSError as error:
        with suppress(OSError):
            os.close(descriptor)
        _raise_snapshot_failure(path, error)

    expected_type = (
        stat.S_ISDIR(initial.st_mode) if directory else stat.S_ISREG(initial.st_mode)
    )
    if not expected_type:
        with suppress(OSError):
            os.close(descriptor)
        description = "directory" if directory else "regular file"
        error = OSError(errno.EINVAL, f"path component is not a {description}: {path}")
        _raise_snapshot_failure(path, error)

    return _PinnedEntry(parent_fd, name, descriptor, initial)


def _identity_fingerprint(status: os.stat_result) -> tuple[int, ...]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_nlink,
        status.st_uid,
        status.st_gid,
    )


def _content_fingerprint(status: os.stat_result) -> tuple[int, ...]:
    return (
        *_identity_fingerprint(status),
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _assert_entry_unchanged(
    entry: _PinnedEntry,
    *,
    path: str,
    compare_content: bool,
) -> None:
    try:
        descriptor_status = os.fstat(entry.fd)
        if entry.name == "/":
            entry_status = os.stat("/", follow_symlinks=False)
        else:
            entry_status = os.stat(
                entry.name,
                dir_fd=entry.parent_fd,
                follow_symlinks=False,
            )
    except OSError as error:
        raise FileChangedError(f"file changed during snapshot: {path}") from error

    descriptor_unchanged = (
        _content_fingerprint(descriptor_status) == _content_fingerprint(entry.initial)
        if compare_content
        else _identity_fingerprint(descriptor_status)
        == _identity_fingerprint(entry.initial)
    )
    entry_unchanged = _identity_fingerprint(entry_status) == _identity_fingerprint(
        entry.initial
    )
    if not descriptor_unchanged or not entry_unchanged:
        raise FileChangedError(f"file changed during snapshot: {path}")


def _read_stable_absolute(
    path: str,
    *,
    chunk_size: int,
    max_bytes: int | None = None,
) -> tuple[Sha256Digest, bytes | None, bool]:
    _validate_external_path(path)
    _validate_chunk_size(chunk_size)

    components = path.split("/")[1:]
    if not components or components == [""]:
        error = OSError(errno.EISDIR, f"path is not a regular file: {path}")
        _raise_snapshot_failure(path, error)

    pinned: list[_PinnedEntry] = []
    completed = False
    try:
        try:
            root_fd = os.open("/", _DIRECTORY_OPEN_FLAGS)
        except OSError as error:
            _raise_snapshot_failure(path, error)
        try:
            root_status = os.fstat(root_fd)
        except OSError as error:
            with suppress(OSError):
                os.close(root_fd)
            _raise_snapshot_failure(path, error)
        pinned.append(_PinnedEntry(-1, "/", root_fd, root_status))

        for component in components[:-1]:
            pinned.append(
                _open_component(
                    pinned[-1].fd,
                    component,
                    directory=True,
                    path=path,
                )
            )
        leaf = _open_component(
            pinned[-1].fd,
            components[-1],
            directory=False,
            path=path,
        )
        pinned.append(leaf)

        hasher = hashlib.sha256()
        retained = bytearray()
        exceeded = False
        while True:
            try:
                chunk = _read_chunk(leaf.fd, chunk_size)
            except OSError as error:
                raise FileChangedError(
                    f"file changed during snapshot: {path}"
                ) from error
            if not chunk:
                break
            hasher.update(chunk)
            if max_bytes is not None:
                remaining = max_bytes - len(retained)
                if len(chunk) <= remaining:
                    retained.extend(chunk)
                else:
                    if remaining > 0:
                        retained.extend(chunk[:remaining])
                    exceeded = True

        for entry in reversed(pinned):
            _assert_entry_unchanged(
                entry,
                path=path,
                compare_content=entry is leaf,
            )

        content = bytes(retained) if max_bytes is not None else None
        result = Sha256Digest(hasher.hexdigest()), content, exceeded
        completed = True
    finally:
        close_error: OSError | None = None
        for entry in reversed(pinned):
            try:
                os.close(entry.fd)
            except OSError as error:
                if close_error is None:
                    close_error = error
        if completed and close_error is not None:
            _raise_snapshot_failure(path, close_error)

    return result


def _local_absolute_path(root: Path, relative_path: str) -> str:
    root_path = os.fspath(root)
    _validate_external_path(root_path)
    _validate_local_path(relative_path)
    if root_path == "/":
        return f"/{relative_path}"
    return f"{root_path}/{relative_path}"


def _require_kind(expected: FileIdentity, kind: PathKind) -> None:
    if expected.kind is not kind:
        raise InvalidLineagePathError(
            f"file validation requires a {kind.value} lineage path"
        )


def _raise_hash_mismatch(identity: FileIdentity) -> None:
    raise HashMismatchError(f"sha256 mismatch for lineage path: {identity.path}")


def sha256_bytes(data: bytes) -> Sha256Digest:
    """Return the canonical SHA-256 digest of exact bytes."""

    return Sha256Digest(hashlib.sha256(data).hexdigest())


def sha256_file(path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Sha256Digest:
    """Hash one normalized absolute regular file through a stable snapshot."""

    digest, _, _ = _read_stable_absolute(
        os.fspath(path),
        chunk_size=chunk_size,
    )
    return digest


def snapshot_local_file(
    root: Path,
    relative_path: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> FileIdentity:
    """Snapshot one canonical relative file beneath an explicit absolute root."""

    absolute_path = _local_absolute_path(root, relative_path)
    digest, _, _ = _read_stable_absolute(absolute_path, chunk_size=chunk_size)
    return FileIdentity(PathKind.LOCAL, relative_path, digest)


def snapshot_external_file(
    path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> FileIdentity:
    """Snapshot one normalized absolute external file."""

    path_text = os.fspath(path)
    digest, _, _ = _read_stable_absolute(path_text, chunk_size=chunk_size)
    return FileIdentity(PathKind.EXTERNAL, path_text, digest)


def validate_local_file(
    root: Path,
    expected: FileIdentity,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> FileIdentity:
    """Verify a fresh local snapshot against its declared exact-byte digest."""

    _require_kind(expected, PathKind.LOCAL)
    actual = snapshot_local_file(root, expected.path, chunk_size=chunk_size)
    if actual.sha256 != expected.sha256:
        _raise_hash_mismatch(expected)
    return actual


def validate_external_file(
    expected: FileIdentity,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> FileIdentity:
    """Verify a fresh external snapshot against its declared exact-byte digest."""

    _require_kind(expected, PathKind.EXTERNAL)
    actual = snapshot_external_file(Path(expected.path), chunk_size=chunk_size)
    if actual.sha256 != expected.sha256:
        _raise_hash_mismatch(expected)
    return actual


def _read_verified_local_bytes(  # pyright: ignore[reportUnusedFunction]
    root: Path,
    expected: FileIdentity,
    max_bytes: int,
    chunk_size: int,
) -> bytes:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ManifestValidationError("max_bytes must be a positive integer")
    _require_kind(expected, PathKind.LOCAL)
    absolute_path = _local_absolute_path(root, expected.path)
    digest, content, exceeded = _read_stable_absolute(
        absolute_path,
        chunk_size=chunk_size,
        max_bytes=max_bytes,
    )
    if exceeded:
        raise ManifestValidationError(
            f"manifest exceeds maximum byte size: {expected.path}"
        )
    if digest != expected.sha256:
        _raise_hash_mismatch(expected)
    if content is None:  # pragma: no cover - max_bytes always requests retention.
        raise RuntimeError("verified byte snapshot did not retain content")
    return content
