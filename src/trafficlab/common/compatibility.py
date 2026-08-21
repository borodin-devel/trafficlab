"""Exact content identities and ordered stage-compatibility checks."""

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from trafficlab.common.errors import TrafficlabError

_HASH_CHUNK_SIZE = 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
type _FileState = tuple[int, int, int, int, int]
type _TreeState = tuple[int, int, int, int, int, int]
type _TreeEntry = tuple[str, str, _TreeState]


@dataclass(frozen=True, slots=True)
class ContentIdentity:
    """The portable size and SHA-256 identity of authoritative bytes."""

    size: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self.size) is not int:
            raise TypeError("size must be a nonnegative integer")
        if self.size < 0:
            raise ValueError("size must be a nonnegative integer")
        if type(self.sha256) is not str:
            raise TypeError("sha256 must be a lowercase 64-character hexadecimal digest")
        if _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest")

    def as_dict(self) -> dict[str, int | str]:
        """Return the canonical JSON-safe identity document."""
        return {"size": self.size, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: object, *, name: str = "content") -> "ContentIdentity":
        """Strictly parse one canonical nested identity document."""
        if type(value) is not dict:
            raise TypeError(f"{name} identity must be an object")
        document = cast(dict[str, object], value)
        if set(document) != {"size", "sha256"}:
            raise ValueError(f"{name} identity must contain exactly size and sha256")
        size = document["size"]
        digest = document["sha256"]
        if type(size) is not int:
            raise TypeError(f"{name} identity size must be a nonnegative integer")
        if size < 0:
            raise ValueError(f"{name} identity size must be a nonnegative integer")
        if type(digest) is not str:
            raise TypeError(f"{name} identity sha256 must be a lowercase 64-character hexadecimal digest")
        if _SHA256_PATTERN.fullmatch(digest) is None:
            raise ValueError(f"{name} identity sha256 must be a lowercase 64-character hexadecimal digest")
        return cls(size=size, sha256=digest)


def identify_bytes(content: bytes) -> ContentIdentity:
    """Identify one exact in-memory byte string."""
    if type(content) is not bytes:
        raise TypeError("content must be bytes")
    return ContentIdentity(size=len(content), sha256=hashlib.sha256(content).hexdigest())


def _file_state(status: os.stat_result) -> _FileState:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def _tree_state(status: os.stat_result) -> _TreeState:
    return (*_file_state(status), status.st_mode)


def _unstable_file_error(path: Path) -> TrafficlabError:
    return TrafficlabError(
        f"file changed while its content identity was being computed: {path}",
        corrective_action="stop concurrent writes and recompute the content identity before reusing the artifact",
    )


def _path_argument(value: object) -> Path:
    if not isinstance(value, Path):
        raise TypeError("path must be a Path")
    return value


def identify_file(path: Path) -> ContentIdentity:
    """Hash one stable regular file and bind the digest to its exact byte count."""
    path = _path_argument(path)

    digest = hashlib.sha256()
    byte_count = 0
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise TrafficlabError(
                    f"cannot identify non-regular file: {path}",
                    corrective_action="provide a readable regular file and retry without reusing the artifact",
                )
            while chunk := stream.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
                byte_count += len(chunk)
            after = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)
    except OSError as error:
        raise TrafficlabError(
            f"could not identify file {path}: {error}",
            corrective_action="verify the path names a readable regular file and retry without reusing the artifact",
        ) from error

    states_stable = _file_state(before) == _file_state(after) == _file_state(current)
    size_stable = byte_count == after.st_size
    if not all((states_stable, size_stable)):
        raise _unstable_file_error(path)
    return ContentIdentity(size=byte_count, sha256=digest.hexdigest())


def _directory_snapshot(root: Path) -> tuple[_TreeEntry, ...]:
    """Return one type-sensitive UTF-8-ordered directory inventory."""
    pending = [root]
    entries: list[_TreeEntry] = []
    while pending:
        directory = pending.pop()
        try:
            children = tuple(directory.iterdir())
        except OSError as error:
            raise TrafficlabError(
                f"could not inspect mounted directory identity {root}: {error}",
                corrective_action="provide a stable readable directory containing only regular files and directories",
            ) from error
        for child in children:
            try:
                relative = child.relative_to(root).as_posix()
                relative_key = relative.encode("utf-8")
                status = child.stat(follow_symlinks=False)
            except (OSError, UnicodeEncodeError) as error:
                raise TrafficlabError(
                    f"could not inspect mounted directory identity {root}: {error}",
                    corrective_action=(
                        "provide a stable readable UTF-8 directory containing only regular files and directories"
                    ),
                ) from error
            if stat.S_ISDIR(status.st_mode):
                kind = "directory"
                pending.append(child)
            elif stat.S_ISREG(status.st_mode):
                kind = "file"
            else:
                raise TrafficlabError(
                    f"mounted directory identity {root} requires only regular files and directories: {relative}",
                    corrective_action="replace links and nonregular entries with stable regular input files",
                )
            entries.append((relative_key.decode("utf-8"), kind, _tree_state(status)))
    return tuple(sorted(entries, key=lambda entry: entry[0].encode("utf-8")))


def identify_directory(path: Path) -> ContentIdentity:
    """Identify one stable directory from its relative regular-file inventory and exact bytes."""
    root = _path_argument(path)
    try:
        root_status = root.stat(follow_symlinks=False)
    except OSError as error:
        raise TrafficlabError(
            f"could not identify mounted directory {root}: {error}",
            corrective_action="provide a stable readable directory containing only regular files and directories",
        ) from error
    if not stat.S_ISDIR(root_status.st_mode):
        raise TrafficlabError(
            f"cannot identify non-directory mounted input: {root}",
            corrective_action="provide a stable readable directory containing only regular files and directories",
        )

    before = _directory_snapshot(root)
    records: list[dict[str, object]] = []
    total_size = 0
    for relative, kind, _state in before:
        record: dict[str, object] = {"kind": kind, "path": relative}
        if kind == "file":
            identity = identify_file(root / relative)
            record.update(identity.as_dict())
            total_size += identity.size
        records.append(record)
    try:
        current_root = root.stat(follow_symlinks=False)
    except OSError as error:
        raise _unstable_file_error(root) from error
    after = _directory_snapshot(root)
    if _tree_state(root_status) != _tree_state(current_root) or before != after:
        raise TrafficlabError(
            f"directory changed while its content identity was being computed: {root}",
            corrective_action="stop concurrent writes and recompute the directory identity before capture",
        )
    canonical_inventory = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return ContentIdentity(size=total_size, sha256=hashlib.sha256(canonical_inventory).hexdigest())


def _compatibility_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"stage compatibility mismatch: {detail}",
        corrective_action="start a fresh stage from compatible authoritative inputs instead of reusing this artifact",
    )


def _validate_field_name(name: object) -> str:
    if type(name) is not str:
        raise TypeError("compatibility field names must be strings")
    if not name:
        raise ValueError("compatibility field names must be nonempty")
    return name


def _mapping_argument(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def require_compatible(expected: Mapping[str, object], actual: Mapping[str, object]) -> None:
    """Require exact records, reporting the first field in expected order that differs."""
    expected = _mapping_argument(expected, name="expected")
    actual = _mapping_argument(actual, name="actual")

    for raw_name, expected_value in expected.items():
        name = _validate_field_name(raw_name)
        if name not in actual:
            raise _compatibility_error(f"missing field {name!r}")
        actual_value = actual[name]
        if type(expected_value) is not type(actual_value) or expected_value != actual_value:
            raise _compatibility_error(f"field {name!r}: expected {expected_value!r}, got {actual_value!r}")

    for raw_name in actual:
        name = _validate_field_name(raw_name)
        if name not in expected:
            raise _compatibility_error(f"unexpected field {name!r}")
