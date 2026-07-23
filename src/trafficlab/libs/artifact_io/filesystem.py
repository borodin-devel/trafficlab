"""Pinned Linux filesystem boundaries for Artifact I/O transactions."""

from __future__ import annotations

import ctypes
import errno
import io
import os
import posixpath
import re
import secrets
import stat
from collections.abc import Buffer
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Never, cast

from trafficlab.libs.lineage import DEFAULT_CHUNK_SIZE, MAX_CHUNK_SIZE

from .errors import (
    ArtifactStatusSecurityError,
    ArtifactWriteError,
    AtomicPublicationError,
    MissingArtifactStatusError,
    PublicationConflictError,
    UnsupportedAtomicPublicationError,
)
from .values import ARTIFACT_STATUS_NAME, MAX_ARTIFACT_STATUS_BYTES

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_STATUS_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_PACKAGE_SOURCE_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_STAGING_OPEN_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
)
_RENAME_NOREPLACE = 1
_UNSUPPORTED_RENAME_ERRNOS = frozenset(
    {errno.EXDEV, errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}
)

_LIBC = ctypes.CDLL(None, use_errno=True)
try:
    _renameat2_symbol = _LIBC.renameat2
except AttributeError:
    _renameat2_symbol = None
else:
    _renameat2_symbol.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    _renameat2_symbol.restype = ctypes.c_int

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_STAGING_COLLISIONS = 64


@dataclass(frozen=True, slots=True)
class _PinnedEntry:
    parent_fd: int | None
    name: str
    fd: int
    initial: os.stat_result


@dataclass(frozen=True, slots=True)
class _PinnedDirectory:
    path: Path
    entries: tuple[_PinnedEntry, ...]

    @property
    def fd(self) -> int:
        return self.entries[-1].fd


@dataclass(frozen=True, slots=True)
class _StagedFile:
    parent: _PinnedDirectory
    name: str
    path: Path
    initial: os.stat_result


@dataclass(frozen=True, slots=True)
class _PackageDirectoryEntry:
    relative_path: str
    parent_fd: int
    name: str
    fd: int
    initial: os.stat_result


@dataclass(slots=True)
class _StagedDirectory:
    parent: _PinnedDirectory
    name: str
    path: Path
    fd: int
    initial: os.stat_result
    directories: list[_PackageDirectoryEntry]
    files: list[_StagedPackageFile]


@dataclass(frozen=True, slots=True)
class _StagedPackageFile:
    root: _StagedDirectory
    relative_path: str
    parent_fd: int
    name: str
    path: Path
    initial: os.stat_result


@dataclass(frozen=True, slots=True)
class _PinnedFile:
    parent: _PinnedDirectory
    name: str
    path: Path
    fd: int
    initial: os.stat_result


class _PinnedFileReadError(Exception):
    """Internal marker separating source-read failures from destination writes."""


class _UnbufferedWriter(io.RawIOBase):
    """One unbuffered descriptor whose lifetime remains library-owned."""

    __slots__ = ("_descriptor",)

    def __init__(self, descriptor: int) -> None:
        super().__init__()
        self._descriptor: int | None = descriptor

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        descriptor = self._descriptor
        if self.closed or descriptor is None:
            raise ValueError("I/O operation on closed publication file")
        return descriptor

    def write(self, data: Buffer) -> int:
        return _write_fd(self.fileno(), data)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed publication file")

    def close(self) -> None:
        if self.closed:
            return
        descriptor = self._descriptor
        self._descriptor = None
        close_error: OSError | None = None
        if descriptor is not None:
            try:
                _close_fd(descriptor)
            except OSError as error:
                close_error = error
        super().close()
        if close_error is not None:
            raise close_error


def _open_fd(
    path: str,
    flags: int,
    *,
    dir_fd: int | None = None,
    mode: int = 0o777,
) -> int:
    if dir_fd is None:
        return os.open(path, flags, mode)
    return os.open(path, flags, mode, dir_fd=dir_fd)


def _fstat_fd(descriptor: int) -> os.stat_result:
    return os.fstat(descriptor)


def _stat_entry(path: str, *, dir_fd: int | None = None) -> os.stat_result:
    if dir_fd is None:
        return os.stat(path, follow_symlinks=False)
    return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)


def _mkdir_entry(name: str, *, dir_fd: int, mode: int) -> None:
    os.mkdir(name, mode=mode, dir_fd=dir_fd)


def _list_directory(descriptor: int) -> tuple[str, ...]:
    with os.scandir(descriptor) as entries:
        return tuple(entry.name for entry in entries)


def _read_fd(descriptor: int, count: int) -> bytes:
    return os.read(descriptor, count)


def _write_fd(
    descriptor: int,
    data: Buffer,
) -> int:
    return os.write(descriptor, data)


def _close_fd(descriptor: int) -> None:
    os.close(descriptor)


def _effective_uid() -> int:
    return os.geteuid()


def _publication_token() -> str:
    return secrets.token_hex(16)


def _flush_writer(  # pyright: ignore[reportUnusedFunction]
    handle: BinaryIO,
) -> None:
    handle.flush()


def _close_writer(  # pyright: ignore[reportUnusedFunction]
    handle: BinaryIO,
) -> None:
    handle.close()


def _renameat2_call(
    source_parent_fd: int,
    source_name: bytes,
    destination_parent_fd: int,
    destination_name: bytes,
    flags: int,
) -> None:
    """Invoke the bound Linux renameat2 syscall without an unsafe fallback."""

    if _renameat2_symbol is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    ctypes.set_errno(0)
    result = _renameat2_symbol(
        source_parent_fd,
        source_name,
        destination_parent_fd,
        destination_name,
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number))


def _atomic_rename_noreplace(  # pyright: ignore[reportUnusedFunction]
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
    *,
    source_path: Path,
    orphan_path: Path | None = None,
) -> None:
    """Atomically publish one pinned entry while retaining failures in place."""

    try:
        encoded_source = os.fsencode(source_name)
        encoded_destination = os.fsencode(destination_name)
        _renameat2_call(
            source_parent_fd,
            encoded_source,
            destination_parent_fd,
            encoded_destination,
            _RENAME_NOREPLACE,
        )
    except OSError as error:
        if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise PublicationConflictError(
                "atomic publication destination already exists",
                retained_paths=(source_path,),
                orphan_path=orphan_path,
            ) from error
        if error.errno in _UNSUPPORTED_RENAME_ERRNOS:
            raise UnsupportedAtomicPublicationError(
                "atomic no-replace publication is unsupported",
                retained_paths=(source_path,),
                orphan_path=orphan_path,
            ) from error
        raise AtomicPublicationError(
            "atomic no-replace publication failed",
            retained_paths=(source_path,),
            orphan_path=orphan_path,
        ) from error


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
    entry_fingerprint = (
        _content_fingerprint(entry_status)
        if compare_content
        else _identity_fingerprint(entry_status)
    )
    if (
        descriptor_fingerprint != initial_fingerprint
        or entry_fingerprint != initial_fingerprint
    ):
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


def _close_pinned_directory(  # pyright: ignore[reportUnusedFunction]
    directory: _PinnedDirectory,
) -> OSError | None:
    return _close_all([entry.fd for entry in directory.entries])


def _pin_attempt_directory(  # pyright: ignore[reportUnusedFunction]
    attempt_dir: Path,
) -> _PinnedDirectory:
    attempt_text = _validate_attempt_path(attempt_dir)
    opened: list[int] = []
    try:
        entries = _open_attempt_chain(attempt_text, opened)
    except BaseException:
        _close_all(opened)
        raise
    return _PinnedDirectory(attempt_dir, tuple(entries))


def _pin_owned_directory(  # pyright: ignore[reportUnusedFunction]
    path: Path,
) -> _PinnedDirectory:
    opened: list[int] = []
    try:
        root_fd = _open_fd("/", _DIRECTORY_OPEN_FLAGS)
        opened.append(root_fd)
        root_status = _fstat_fd(root_fd)
        _require_directory(root_status)
        entries = [_PinnedEntry(None, "/", root_fd, root_status)]
        for component in path.as_posix().split("/")[1:]:
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
        final = entries[-1].initial
        if final.st_uid != _effective_uid():
            raise OSError(errno.EPERM, "publication parent owner is unsafe")
    except BaseException:
        _close_all(opened)
        raise
    return _PinnedDirectory(path, tuple(entries))


def _revalidate_pinned_directory(directory: _PinnedDirectory) -> None:
    for entry in reversed(directory.entries):
        descriptor_status = _fstat_fd(entry.fd)
        entry_status = _stat_entry(entry.name, dir_fd=entry.parent_fd)
        if _identity_fingerprint(descriptor_status) != _identity_fingerprint(
            entry.initial
        ) or _identity_fingerprint(entry_status) != _identity_fingerprint(
            entry.initial
        ):
            raise OSError(errno.ESTALE, "pinned publication directory changed")


def _entry_exists(  # pyright: ignore[reportUnusedFunction]
    directory: _PinnedDirectory,
    name: str,
) -> bool:
    try:
        _stat_entry(name, dir_fd=directory.fd)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return False
        raise
    return True


def _require_private_staged_file(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != _effective_uid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise OSError(
            errno.EPERM,
            "staged publication file owner, mode, type, or links are unsafe",
        )


def _create_staged_file(  # pyright: ignore[reportUnusedFunction]
    parent: _PinnedDirectory,
    destination_name: str,
) -> tuple[_StagedFile, BinaryIO]:
    _revalidate_pinned_directory(parent)
    for _ in range(_MAX_STAGING_COLLISIONS):
        token = _publication_token()
        if _TOKEN_PATTERN.fullmatch(token) is None:
            raise OSError(errno.EINVAL, "publication token is invalid")
        name = f".trafficlab-staging-{token}"
        if name == destination_name:
            continue
        path = parent.path / name
        try:
            descriptor = _open_fd(
                name,
                _STAGING_OPEN_FLAGS,
                dir_fd=parent.fd,
                mode=0o600,
            )
        except OSError as error:
            if error.errno == errno.EEXIST:
                continue
            raise
        try:
            metadata = _fstat_fd(descriptor)
            entry_metadata = _stat_entry(name, dir_fd=parent.fd)
            _require_private_staged_file(metadata)
            if _content_fingerprint(entry_metadata) != _content_fingerprint(metadata):
                raise OSError(errno.ESTALE, "staged publication binding changed")
            writer = cast(BinaryIO, _UnbufferedWriter(descriptor))
        except BaseException as error:
            with suppress(OSError):
                _close_fd(descriptor)
            raise ArtifactWriteError(
                "cannot establish private publication staging",
                retained_paths=(path,),
            ) from error
        return _StagedFile(parent, name, path, metadata), writer
    raise OSError(errno.EEXIST, "private publication staging names collided")


def _revalidate_staged_file(staged: _StagedFile) -> os.stat_result:
    _revalidate_pinned_directory(staged.parent)
    metadata = _stat_entry(staged.name, dir_fd=staged.parent.fd)
    _require_private_staged_file(metadata)
    if (
        metadata.st_dev != staged.initial.st_dev
        or metadata.st_ino != staged.initial.st_ino
    ):
        raise OSError(errno.ESTALE, "staged publication binding changed")
    return metadata


def _revalidate_published_file(  # pyright: ignore[reportUnusedFunction]
    parent: _PinnedDirectory,
    destination_name: str,
    staged: _StagedFile,
) -> os.stat_result:
    _revalidate_pinned_directory(parent)
    metadata = _stat_entry(destination_name, dir_fd=parent.fd)
    _require_private_staged_file(metadata)
    if (
        metadata.st_dev != staged.initial.st_dev
        or metadata.st_ino != staged.initial.st_ino
    ):
        raise OSError(errno.ESTALE, "published artifact binding changed")
    return metadata


def _directory_binding_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _revalidate_pinned_directory_binding(directory: _PinnedDirectory) -> None:
    """Revalidate a chain while allowing owned child-directory link changes."""

    for entry in reversed(directory.entries):
        descriptor_status = _fstat_fd(entry.fd)
        entry_status = _stat_entry(entry.name, dir_fd=entry.parent_fd)
        expected = _directory_binding_fingerprint(entry.initial)
        if (
            _directory_binding_fingerprint(descriptor_status) != expected
            or _directory_binding_fingerprint(entry_status) != expected
        ):
            raise OSError(errno.ESTALE, "pinned package directory changed")


def _require_private_staged_directory(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != _effective_uid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise OSError(
            errno.EPERM,
            "staged package directory owner, mode, or type is unsafe",
        )


def _create_staged_directory(  # pyright: ignore[reportUnusedFunction]
    parent: _PinnedDirectory,
    destination_name: str,
) -> _StagedDirectory:
    _revalidate_pinned_directory(parent)
    for _ in range(_MAX_STAGING_COLLISIONS):
        token = _publication_token()
        if _TOKEN_PATTERN.fullmatch(token) is None:
            raise OSError(errno.EINVAL, "publication token is invalid")
        name = f".trafficlab-staging-{token}"
        if name == destination_name:
            continue
        path = parent.path / name
        try:
            _mkdir_entry(name, dir_fd=parent.fd, mode=0o700)
        except OSError as error:
            if error.errno == errno.EEXIST:
                continue
            raise

        descriptor: int | None = None
        try:
            descriptor = _open_fd(
                name,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=parent.fd,
            )
            metadata = _fstat_fd(descriptor)
            entry_metadata = _stat_entry(name, dir_fd=parent.fd)
            _require_private_staged_directory(metadata)
            if _directory_binding_fingerprint(
                entry_metadata
            ) != _directory_binding_fingerprint(metadata):
                raise OSError(errno.ESTALE, "staged package binding changed")
        except BaseException as error:
            if descriptor is not None:
                with suppress(OSError):
                    _close_fd(descriptor)
            raise ArtifactWriteError(
                "cannot establish private package staging",
                retained_paths=(path,),
            ) from error
        return _StagedDirectory(
            parent=parent,
            name=name,
            path=path,
            fd=descriptor,
            initial=metadata,
            directories=[],
            files=[],
        )
    raise OSError(errno.EEXIST, "private package staging names collided")


def _package_directory_map(staged: _StagedDirectory) -> dict[str, int]:
    return {
        "": staged.fd,
        **{entry.relative_path: entry.fd for entry in staged.directories},
    }


def _implied_directories(member_paths: tuple[str, ...]) -> tuple[str, ...]:
    implied: set[str] = set()
    for member_path in member_paths:
        components = member_path.split("/")[:-1]
        for count in range(1, len(components) + 1):
            implied.add("/".join(components[:count]))
    return tuple(sorted(implied))


def _create_package_directories(  # pyright: ignore[reportUnusedFunction]
    staged: _StagedDirectory,
    member_paths: tuple[str, ...],
) -> None:
    _revalidate_pinned_directory_binding(staged.parent)
    for relative_path in _implied_directories(member_paths):
        directories = _package_directory_map(staged)
        parent_path, _, name = relative_path.rpartition("/")
        parent_fd = directories[parent_path]
        _mkdir_entry(name, dir_fd=parent_fd, mode=0o700)
        descriptor: int | None = None
        try:
            descriptor = _open_fd(
                name,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=parent_fd,
            )
            metadata = _fstat_fd(descriptor)
            entry_metadata = _stat_entry(name, dir_fd=parent_fd)
            _require_private_staged_directory(metadata)
            if _directory_binding_fingerprint(
                entry_metadata
            ) != _directory_binding_fingerprint(metadata):
                raise OSError(errno.ESTALE, "nested package binding changed")
        except BaseException:
            if descriptor is not None:
                with suppress(OSError):
                    _close_fd(descriptor)
            raise
        staged.directories.append(
            _PackageDirectoryEntry(
                relative_path,
                parent_fd,
                name,
                descriptor,
                metadata,
            )
        )
    _revalidate_staged_directory(staged)


def _revalidate_package_directories(staged: _StagedDirectory) -> None:
    for entry in staged.directories:
        descriptor_status = _fstat_fd(entry.fd)
        entry_status = _stat_entry(entry.name, dir_fd=entry.parent_fd)
        _require_private_staged_directory(descriptor_status)
        expected = _directory_binding_fingerprint(entry.initial)
        if (
            _directory_binding_fingerprint(descriptor_status) != expected
            or _directory_binding_fingerprint(entry_status) != expected
        ):
            raise OSError(errno.ESTALE, "nested package directory changed")


def _revalidate_staged_directory(  # pyright: ignore[reportUnusedFunction]
    staged: _StagedDirectory,
) -> None:
    _revalidate_pinned_directory_binding(staged.parent)
    descriptor_status = _fstat_fd(staged.fd)
    entry_status = _stat_entry(staged.name, dir_fd=staged.parent.fd)
    _require_private_staged_directory(descriptor_status)
    expected = _directory_binding_fingerprint(staged.initial)
    if (
        _directory_binding_fingerprint(descriptor_status) != expected
        or _directory_binding_fingerprint(entry_status) != expected
    ):
        raise OSError(errno.ESTALE, "staged package root changed")
    _revalidate_package_directories(staged)


def _revalidate_published_directory(  # pyright: ignore[reportUnusedFunction]
    parent: _PinnedDirectory,
    destination_name: str,
    staged: _StagedDirectory,
) -> None:
    _revalidate_pinned_directory_binding(parent)
    descriptor_status = _fstat_fd(staged.fd)
    entry_status = _stat_entry(destination_name, dir_fd=parent.fd)
    _require_private_staged_directory(descriptor_status)
    expected = _directory_binding_fingerprint(staged.initial)
    if (
        _directory_binding_fingerprint(descriptor_status) != expected
        or _directory_binding_fingerprint(entry_status) != expected
    ):
        raise OSError(errno.ESTALE, "published package root changed")
    _revalidate_package_directories(staged)


def _create_package_file(  # pyright: ignore[reportUnusedFunction]
    staged: _StagedDirectory,
    relative_path: str,
) -> tuple[_StagedPackageFile, BinaryIO]:
    _revalidate_staged_directory(staged)
    parent_path, _, name = relative_path.rpartition("/")
    parent_fd = _package_directory_map(staged)[parent_path]
    path = staged.path / relative_path
    descriptor = _open_fd(
        name,
        _STAGING_OPEN_FLAGS,
        dir_fd=parent_fd,
        mode=0o600,
    )
    try:
        metadata = _fstat_fd(descriptor)
        entry_metadata = _stat_entry(name, dir_fd=parent_fd)
        _require_private_staged_file(metadata)
        if _content_fingerprint(entry_metadata) != _content_fingerprint(metadata):
            raise OSError(errno.ESTALE, "package member binding changed")
        package_file = _StagedPackageFile(
            staged,
            relative_path,
            parent_fd,
            name,
            path,
            metadata,
        )
        writer = cast(BinaryIO, _UnbufferedWriter(descriptor))
    except BaseException as error:
        with suppress(OSError):
            _close_fd(descriptor)
        raise ArtifactWriteError(
            "cannot establish private package member",
            retained_paths=(staged.path,),
        ) from error
    staged.files.append(package_file)
    return package_file, writer


def _revalidate_package_file(  # pyright: ignore[reportUnusedFunction]
    package_file: _StagedPackageFile,
) -> os.stat_result:
    _revalidate_staged_directory(package_file.root)
    metadata = _stat_entry(package_file.name, dir_fd=package_file.parent_fd)
    _require_private_staged_file(metadata)
    if (
        metadata.st_dev != package_file.initial.st_dev
        or metadata.st_ino != package_file.initial.st_ino
    ):
        raise OSError(errno.ESTALE, "package member binding changed")
    return metadata


def _validate_package_tree(  # pyright: ignore[reportUnusedFunction]
    staged: _StagedDirectory,
    expected_files: tuple[str, ...],
    *,
    published_name: str | None = None,
) -> None:
    if published_name is None:
        _revalidate_staged_directory(staged)
    else:
        _revalidate_published_directory(staged.parent, published_name, staged)

    expected_file_set = set(expected_files)
    expected_directory_set = set(_implied_directories(expected_files))
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    directories = (
        ("", staged.fd),
        *((entry.relative_path, entry.fd) for entry in staged.directories),
    )
    for relative_parent, descriptor in sorted(directories):
        for name in sorted(_list_directory(descriptor)):
            relative_path = f"{relative_parent}/{name}" if relative_parent else name
            metadata = _stat_entry(name, dir_fd=descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                _require_private_staged_directory(metadata)
                actual_directories.add(relative_path)
            elif stat.S_ISREG(metadata.st_mode):
                _require_private_staged_file(metadata)
                actual_files.add(relative_path)
            else:
                raise OSError(errno.EPERM, "package contains an unsafe entry type")

    if actual_directories != expected_directory_set:
        raise OSError(errno.EINVAL, "package directory membership is not exact")
    if actual_files != expected_file_set:
        raise OSError(errno.EINVAL, "package file membership is not exact")
    recorded = {
        package_file.relative_path: package_file for package_file in staged.files
    }
    if set(recorded) != expected_file_set:
        raise OSError(errno.EINVAL, "package planned-file membership is not exact")
    for relative_path in sorted(expected_file_set):
        package_file = recorded[relative_path]
        metadata = _stat_entry(package_file.name, dir_fd=package_file.parent_fd)
        _require_private_staged_file(metadata)
        if (
            metadata.st_dev != package_file.initial.st_dev
            or metadata.st_ino != package_file.initial.st_ino
        ):
            raise OSError(errno.ESTALE, "package member binding changed")


def _open_pinned_file(  # pyright: ignore[reportUnusedFunction]
    parent: _PinnedDirectory,
    name: str,
    path: Path,
) -> _PinnedFile:
    _revalidate_pinned_directory_binding(parent)
    descriptor = _open_fd(
        name,
        _PACKAGE_SOURCE_OPEN_FLAGS,
        dir_fd=parent.fd,
    )
    try:
        metadata = _fstat_fd(descriptor)
        entry_metadata = _stat_entry(name, dir_fd=parent.fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(errno.EINVAL, "package copy source is not a regular file")
        if _content_fingerprint(entry_metadata) != _content_fingerprint(metadata):
            raise OSError(errno.ESTALE, "package copy source binding changed")
    except BaseException:
        with suppress(OSError):
            _close_fd(descriptor)
        raise
    return _PinnedFile(parent, name, path, descriptor, metadata)


def _copy_pinned_file(  # pyright: ignore[reportUnusedFunction]
    source: _PinnedFile,
    destination: BinaryIO,
    *,
    chunk_size: int,
) -> None:
    while True:
        try:
            chunk = _read_fd(source.fd, chunk_size)
            if type(chunk) is not bytes or len(chunk) > chunk_size:
                raise OSError(errno.EIO, "package copy read returned invalid bytes")
        except BaseException as error:
            raise _PinnedFileReadError("package copy source read failed") from error
        if not chunk:
            return
        offset = 0
        while offset < len(chunk):
            written = destination.write(chunk[offset:])
            if type(written) is not int or not 1 <= written <= len(chunk) - offset:
                raise OSError(errno.EIO, "package copy write returned invalid count")
            offset += written


def _revalidate_pinned_file(  # pyright: ignore[reportUnusedFunction]
    source: _PinnedFile,
) -> None:
    descriptor_status = _fstat_fd(source.fd)
    entry_status = _stat_entry(source.name, dir_fd=source.parent.fd)
    if _content_fingerprint(descriptor_status) != _content_fingerprint(
        source.initial
    ) or _content_fingerprint(entry_status) != _content_fingerprint(source.initial):
        raise OSError(errno.ESTALE, "package copy source changed")
    _revalidate_pinned_directory_binding(source.parent)


def _close_pinned_file(  # pyright: ignore[reportUnusedFunction]
    source: _PinnedFile,
) -> OSError | None:
    try:
        _close_fd(source.fd)
    except OSError as error:
        return error
    return None


def _close_staged_directory(  # pyright: ignore[reportUnusedFunction]
    staged: _StagedDirectory,
) -> OSError | None:
    return _close_all([staged.fd, *(entry.fd for entry in staged.directories)])


def _snapshot_staged_status(  # pyright: ignore[reportUnusedFunction]
    staged: _StagedFile,
    *,
    max_bytes: int,
    chunk_size: int,
) -> bytes:
    _revalidate_staged_file(staged)
    descriptor = _open_fd(
        staged.name,
        _STATUS_OPEN_FLAGS,
        dir_fd=staged.parent.fd,
    )
    completed = False
    try:
        initial = _fstat_fd(descriptor)
        _require_private_staged_file(initial)
        if (
            initial.st_dev != staged.initial.st_dev
            or initial.st_ino != staged.initial.st_ino
        ):
            raise OSError(errno.ESTALE, "staged status binding changed")
        retained = bytearray()
        while True:
            request_size = min(chunk_size, max_bytes + 1 - len(retained))
            chunk = _read_fd(descriptor, request_size)
            if len(chunk) > request_size:
                raise OSError(errno.EIO, "staged status read returned invalid bytes")
            if not chunk:
                break
            retained.extend(chunk)
            if len(retained) > max_bytes:
                raise OSError(errno.EFBIG, "staged status exceeds maximum byte size")
        descriptor_status = _fstat_fd(descriptor)
        entry_status = _stat_entry(staged.name, dir_fd=staged.parent.fd)
        if _content_fingerprint(descriptor_status) != _content_fingerprint(
            initial
        ) or _content_fingerprint(entry_status) != _content_fingerprint(initial):
            raise OSError(errno.ESTALE, "staged status changed during validation")
        _revalidate_pinned_directory(staged.parent)
        completed = True
        return bytes(retained)
    finally:
        try:
            _close_fd(descriptor)
        except OSError:
            if completed:
                raise


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
    if _content_fingerprint(entry_status) != _content_fingerprint(metadata):
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
