"""Canonical status codec and detached publication validation boundary."""

import errno
import os
import tomllib
from pathlib import Path
from typing import cast

from trafficlab.libs.lineage import (
    DEFAULT_CHUNK_SIZE,
    FileIdentity,
    LineageError,
    PathKind,
    Sha256Digest,
    validate_external_file,
    validate_local_file,
)

from . import filesystem
from .errors import (
    ArtifactValidationError,
    InvalidArtifactStatusError,
    InvalidPublicationPlanError,
    OrphanArtifactError,
)
from .values import (
    LAUNCH_NAME,
    MANIFEST_NAME,
    MAX_ARTIFACT_STATUS_BYTES,
    ArtifactKind,
    ArtifactStatus,
    PublicationPlan,
)

_KEYS = (
    "schema_version",
    "state",
    "artifact_kind",
    "artifact_path",
    "digest_path",
    "sha256",
    "launch_path",
    "launch_sha256",
)


def _toml_basic_string(value: str) -> str:
    escaped: list[str] = ['"']
    for character in value:
        codepoint = ord(character)
        if character == '"':
            escaped.append('\\"')
        elif character == "\\":
            escaped.append("\\\\")
        elif codepoint < 0x20 or codepoint == 0x7F:
            escaped.append(f"\\u{codepoint:04X}")
        elif codepoint <= 0x7E:
            escaped.append(character)
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04X}")
        else:
            escaped.append(f"\\U{codepoint:08X}")
    escaped.append('"')
    return "".join(escaped)


def render_artifact_status(status: ArtifactStatus) -> bytes:
    """Render one status with fixed fields, spelling, ordering, and LF ending."""

    if not isinstance(cast(object, status), ArtifactStatus):
        raise InvalidArtifactStatusError("artifact status must be an ArtifactStatus")
    text = (
        "\n".join(
            (
                f"schema_version = {status.schema_version}",
                f"state = {_toml_basic_string(status.state)}",
                f"artifact_kind = {_toml_basic_string(status.artifact_kind.value)}",
                f"artifact_path = {_toml_basic_string(status.artifact_path)}",
                f"digest_path = {_toml_basic_string(status.digest_path)}",
                f"sha256 = {_toml_basic_string(status.sha256.value)}",
                f"launch_path = {_toml_basic_string(status.launch_path)}",
                f"launch_sha256 = {_toml_basic_string(status.launch_sha256.value)}",
            )
        )
        + "\n"
    )
    data = text.encode("utf-8", errors="strict")
    if len(data) > MAX_ARTIFACT_STATUS_BYTES:
        raise InvalidArtifactStatusError("artifact status exceeds the byte limit")
    return data


def _require_exact_types(values: dict[str, object]) -> None:
    if type(values["schema_version"]) is not int:
        raise InvalidArtifactStatusError("artifact status field types are invalid")
    for key in _KEYS[1:]:
        if type(values[key]) is not str:
            raise InvalidArtifactStatusError("artifact status field types are invalid")


def parse_artifact_status(data: bytes) -> ArtifactStatus:
    """Parse only the exact canonical version-1 detached status encoding."""

    if not isinstance(cast(object, data), bytes):
        raise InvalidArtifactStatusError("artifact status input must be bytes")
    if not data:
        raise InvalidArtifactStatusError("artifact status must not be empty")
    if len(data) > MAX_ARTIFACT_STATUS_BYTES:
        raise InvalidArtifactStatusError("artifact status exceeds the byte limit")
    if data.startswith(b"\xef\xbb\xbf") or b"\x00" in data or b"\r" in data:
        raise InvalidArtifactStatusError("artifact status byte envelope is invalid")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidArtifactStatusError("artifact status is not valid UTF-8") from exc
    try:
        loaded = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidArtifactStatusError(
            "artifact status is not valid canonical TOML"
        ) from exc
    values = cast(dict[str, object], loaded)
    if tuple(values) != _KEYS:
        raise InvalidArtifactStatusError(
            "artifact status must contain exactly the canonical ordered keys"
        )
    _require_exact_types(values)
    kind_text = cast(str, values["artifact_kind"])
    try:
        kind = ArtifactKind(kind_text)
        status = ArtifactStatus(
            schema_version=cast(int, values["schema_version"]),
            state=cast(str, values["state"]),
            artifact_kind=kind,
            artifact_path=cast(str, values["artifact_path"]),
            digest_path=cast(str, values["digest_path"]),
            sha256=Sha256Digest(cast(str, values["sha256"])),
            launch_path=cast(str, values["launch_path"]),
            launch_sha256=Sha256Digest(cast(str, values["launch_sha256"])),
        )
    except (InvalidArtifactStatusError, LineageError, ValueError) as exc:
        raise InvalidArtifactStatusError("artifact status fields are invalid") from exc
    if render_artifact_status(status) != data:
        raise InvalidArtifactStatusError("artifact status encoding is not canonical")
    return status


def _require_plan(plan: PublicationPlan) -> None:
    if not isinstance(cast(object, plan), PublicationPlan):
        raise InvalidPublicationPlanError(
            "publication validation requires a PublicationPlan"
        )
    PublicationPlan(
        plan.attempt_dir,
        plan.artifact_path,
        plan.artifact_kind,
        plan.member_paths,
    )


def _lstat_destination(path: Path) -> os.stat_result:
    return os.stat(path, follow_symlinks=False)


def _destination_exists(path: Path) -> bool:
    try:
        _lstat_destination(path)
    except OSError as error:
        if error.errno in {errno.ENOENT, errno.ENOTDIR}:
            return False
        raise ArtifactValidationError(
            "cannot inspect the explicit artifact destination"
        ) from error
    return True


def _raise_orphan(plan: PublicationPlan, cause: BaseException) -> None:
    raise OrphanArtifactError(
        "artifact destination exists without valid detached status",
        orphan_path=plan.artifact_path,
    ) from cause


def _read_bound_status(plan: PublicationPlan, chunk_size: int) -> ArtifactStatus:
    try:
        data = filesystem._snapshot_status(  # pyright: ignore[reportPrivateUsage]
            plan.attempt_dir,
            chunk_size=chunk_size,
        )
        status = parse_artifact_status(data)
        if (
            status.artifact_kind is not plan.artifact_kind
            or status.artifact_path != str(plan.artifact_path)
            or status.launch_path != str(plan.launch_path)
            or (
                status.digest_path
                != (
                    str(plan.artifact_path / MANIFEST_NAME)
                    if plan.artifact_kind is ArtifactKind.PACKAGE
                    else str(plan.artifact_path)
                )
            )
        ):
            raise InvalidArtifactStatusError(
                "detached status does not match the explicit publication plan"
            )
    except InvalidArtifactStatusError as error:
        if _destination_exists(plan.artifact_path):
            _raise_orphan(plan, error)
        raise
    return status


def _validate_launch(
    plan: PublicationPlan,
    status: ArtifactStatus,
    chunk_size: int,
) -> None:
    expected = FileIdentity(
        PathKind.EXTERNAL,
        status.launch_path,
        status.launch_sha256,
    )
    try:
        validate_external_file(expected, chunk_size=chunk_size)
    except LineageError as error:
        _raise_target_validation_failure(
            plan,
            error,
            message="startup record failed detached validation",
        )


def _raise_target_validation_failure(
    plan: PublicationPlan,
    cause: LineageError,
    *,
    message: str,
) -> None:
    if _destination_exists(plan.artifact_path):
        _raise_orphan(plan, cause)
    raise ArtifactValidationError(message) from cause


def _validate_file_artifact(
    plan: PublicationPlan,
    status: ArtifactStatus,
    chunk_size: int,
) -> None:
    expected = FileIdentity(PathKind.EXTERNAL, status.artifact_path, status.sha256)
    try:
        validate_external_file(expected, chunk_size=chunk_size)
    except LineageError as error:
        _raise_target_validation_failure(
            plan,
            error,
            message="published artifact failed detached validation",
        )


def _validate_package_artifact(
    plan: PublicationPlan,
    status: ArtifactStatus,
    chunk_size: int,
) -> None:
    manifest = FileIdentity(PathKind.LOCAL, MANIFEST_NAME, status.sha256)
    copied_launch = FileIdentity(
        PathKind.LOCAL,
        LAUNCH_NAME,
        status.launch_sha256,
    )
    try:
        validate_local_file(
            plan.artifact_path,
            manifest,
            chunk_size=chunk_size,
        )
        validate_local_file(
            plan.artifact_path,
            copied_launch,
            chunk_size=chunk_size,
        )
    except LineageError as error:
        _raise_target_validation_failure(
            plan,
            error,
            message="published artifact failed detached validation",
        )


def validate_publication(
    plan: PublicationPlan,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ArtifactStatus:
    """Validate one explicit artifact through its pinned detached status."""

    _require_plan(plan)
    status = _read_bound_status(plan, chunk_size)
    _validate_launch(plan, status, chunk_size)
    if plan.artifact_kind is ArtifactKind.FILE:
        _validate_file_artifact(plan, status, chunk_size)
    else:
        _validate_package_artifact(plan, status, chunk_size)
    return status
