"""Canonical pure TOML codec for detached artifact status values."""

import tomllib
from typing import cast

from trafficlab.libs.lineage import LineageError, Sha256Digest

from .errors import InvalidArtifactStatusError
from .values import MAX_ARTIFACT_STATUS_BYTES, ArtifactKind, ArtifactStatus

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
