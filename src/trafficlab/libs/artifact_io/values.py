"""Immutable values and pure lexical plans for artifact publication."""

import posixpath
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from trafficlab.libs.lineage import (
    HashDomain,
    HashRegion,
    LineageError,
    Sha256Digest,
    validate_hash_domain,
)

from .errors import InvalidArtifactStatusError, InvalidPublicationPlanError

CURRENT_ARTIFACT_STATUS_VERSION = 1
MAX_ARTIFACT_STATUS_BYTES = 16_384
ARTIFACT_STATUS_NAME = "artifact-status.toml"
LAUNCH_NAME = "launch.toml"
MANIFEST_NAME = "manifest.json"

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED_MEMBERS = frozenset({LAUNCH_NAME, MANIFEST_NAME})


class ArtifactKind(StrEnum):
    """The complete filesystem shape published by one transaction."""

    PACKAGE = "package"
    FILE = "file"


def _is_utf8_encodable(value: str) -> bool:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return True


def _validate_absolute_path(value: object, field: str) -> Path:
    if not isinstance(value, Path):
        raise InvalidPublicationPlanError(f"{field} must be a Path")
    text = value.as_posix()
    if (
        not value.is_absolute()
        or not text
        or text.startswith("//")
        or posixpath.normpath(text) != text
        or any(component in {".", ".."} for component in text.split("/"))
        or (text.endswith("/") and text != "/")
        or _CONTROL_CHARACTER.search(text) is not None
        or not _is_utf8_encodable(text)
    ):
        raise InvalidPublicationPlanError(
            f"{field} must be a canonical absolute POSIX path"
        )
    return value


def _validate_member_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidPublicationPlanError(
            "package member must be a non-empty canonical relative POSIX path"
        )
    if (
        value.startswith("/")
        or value.endswith("/")
        or PurePosixPath(value).as_posix() != value
        or any(component in {".", ".."} for component in value.split("/"))
        or _CONTROL_CHARACTER.search(value) is not None
        or not _is_utf8_encodable(value)
    ):
        raise InvalidPublicationPlanError(
            "package member must be a non-empty canonical relative POSIX path"
        )
    if value in _RESERVED_MEMBERS:
        raise InvalidPublicationPlanError("package member path is reserved")
    return value


def _has_file_prefix_collision(paths: tuple[str, ...]) -> bool:
    components = tuple(tuple(path.split("/")) for path in paths)
    for index, left in enumerate(components):
        for right in components[index + 1 :]:
            shorter, longer = (left, right) if len(left) < len(right) else (right, left)
            if len(shorter) < len(longer) and longer[: len(shorter)] == shorter:
                return True
    return False


def _validate_member_paths(value: object, *, require_sorted: bool) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise InvalidPublicationPlanError(
            "package member paths must be an immutable tuple"
        )
    raw_paths = cast(tuple[object, ...], value)
    paths = tuple(_validate_member_path(path) for path in raw_paths)
    if len(set(paths)) != len(paths):
        raise InvalidPublicationPlanError("duplicate package member path")
    if _has_file_prefix_collision(paths):
        raise InvalidPublicationPlanError(
            "package member paths have a file-prefix collision"
        )
    if require_sorted and paths != tuple(sorted(paths)):
        raise InvalidPublicationPlanError(
            "package member paths must be in canonical order"
        )
    return paths


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    """An explicit immutable destination and expected artifact shape."""

    attempt_dir: Path
    artifact_path: Path
    artifact_kind: ArtifactKind
    member_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        attempt = _validate_absolute_path(cast(object, self.attempt_dir), "attempt_dir")
        artifact = _validate_absolute_path(
            cast(object, self.artifact_path), "artifact_path"
        )
        kind = cast(object, self.artifact_kind)
        if not isinstance(kind, ArtifactKind):
            raise InvalidPublicationPlanError(
                "artifact_kind must be ArtifactKind.PACKAGE or ArtifactKind.FILE"
            )
        members = _validate_member_paths(
            cast(object, self.member_paths), require_sorted=True
        )
        if artifact in {attempt, attempt / LAUNCH_NAME, attempt / ARTIFACT_STATUS_NAME}:
            raise InvalidPublicationPlanError(
                "artifact path must be distinct from attempt, launch, and status paths"
            )
        if kind is ArtifactKind.FILE and members:
            raise InvalidPublicationPlanError(
                "file publication plans cannot have members"
            )

    @property
    def launch_path(self) -> Path:
        """Return the immutable startup-record path derived from the attempt."""

        return self.attempt_dir / LAUNCH_NAME

    @property
    def status_path(self) -> Path:
        """Return the fixed detached commit-marker path derived from the attempt."""

        return self.attempt_dir / ARTIFACT_STATUS_NAME


@dataclass(frozen=True, slots=True)
class PackageMember:
    """One planned component-owned package member and its callbacks."""

    path: str
    write: Callable[[BinaryIO], None]
    validate: Callable[[Path], None]

    def __post_init__(self) -> None:
        _validate_member_path(cast(object, self.path))
        if not callable(self.write):
            raise InvalidPublicationPlanError("package member write must be callable")
        if not callable(self.validate):
            raise InvalidPublicationPlanError(
                "package member validate must be callable"
            )


def _validate_status_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidArtifactStatusError(
            f"{field} must be a canonical absolute POSIX path"
        )
    if (
        not value.startswith("/")
        or value.startswith("//")
        or posixpath.normpath(value) != value
        or any(component in {".", ".."} for component in value.split("/"))
        or (value.endswith("/") and value != "/")
        or _CONTROL_CHARACTER.search(value) is not None
        or not _is_utf8_encodable(value)
    ):
        raise InvalidArtifactStatusError(
            f"{field} must be a canonical absolute POSIX path"
        )
    return value


def _validate_detached_hash_domain(region: str, covered_path: str) -> None:
    try:
        validate_hash_domain(
            HashDomain(
                carrier=HashRegion(ARTIFACT_STATUS_NAME, region),
                covered=(HashRegion(covered_path),),
            )
        )
    except LineageError as exc:
        raise InvalidArtifactStatusError(
            "artifact status hash domain must be detached"
        ) from exc


@dataclass(frozen=True, slots=True)
class ArtifactStatus:
    """The canonical detached success identity for one published artifact."""

    schema_version: int
    state: str
    artifact_kind: ArtifactKind
    artifact_path: str
    digest_path: str
    sha256: Sha256Digest
    launch_path: str
    launch_sha256: Sha256Digest

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or (
            self.schema_version != CURRENT_ARTIFACT_STATUS_VERSION
        ):
            raise InvalidArtifactStatusError(
                "only artifact status schema version 1 is supported"
            )
        state = cast(object, self.state)
        if not isinstance(state, str) or state != "published":
            raise InvalidArtifactStatusError(
                'artifact status state must be exactly "published"'
            )
        kind = cast(object, self.artifact_kind)
        if not isinstance(kind, ArtifactKind):
            raise InvalidArtifactStatusError(
                "artifact status kind must be ArtifactKind.PACKAGE or ArtifactKind.FILE"
            )
        artifact_path = _validate_status_path(
            cast(object, self.artifact_path), "artifact_path"
        )
        digest_path = _validate_status_path(
            cast(object, self.digest_path), "digest_path"
        )
        launch_path = _validate_status_path(
            cast(object, self.launch_path), "launch_path"
        )
        if not isinstance(cast(object, self.sha256), Sha256Digest):
            raise InvalidArtifactStatusError("sha256 must be a Sha256Digest")
        if not isinstance(cast(object, self.launch_sha256), Sha256Digest):
            raise InvalidArtifactStatusError("launch_sha256 must be a Sha256Digest")
        expected_digest_path = (
            posixpath.join(artifact_path, MANIFEST_NAME)
            if kind is ArtifactKind.PACKAGE
            else artifact_path
        )
        if digest_path != expected_digest_path:
            raise InvalidArtifactStatusError(
                "digest_path does not match the artifact kind and path"
            )
        _validate_detached_hash_domain("sha256", digest_path)
        _validate_detached_hash_domain("launch_sha256", launch_path)


def build_file_plan(attempt_dir: Path, artifact_path: Path) -> PublicationPlan:
    """Build a canonical immutable single-file publication plan."""

    return PublicationPlan(attempt_dir, artifact_path, ArtifactKind.FILE, ())


def build_package_plan(
    attempt_dir: Path,
    artifact_path: Path,
    *,
    members: Iterable[str],
) -> PublicationPlan:
    """Materialize once and canonically order a package publication plan."""

    try:
        materialized = tuple(members)
    except TypeError as exc:
        raise InvalidPublicationPlanError("package members must be iterable") from exc
    validated = _validate_member_paths(materialized, require_sorted=False)
    return PublicationPlan(
        attempt_dir,
        artifact_path,
        ArtifactKind.PACKAGE,
        tuple(sorted(validated)),
    )
