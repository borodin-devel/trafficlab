"""Canonical startup-record rendering and atomic no-overwrite publication."""

from __future__ import annotations

import errno
import json
import os
import posixpath
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from trafficlab.libs.lineage import FileIdentity, snapshot_external_file

from .errors import (
    ConfigurationError,
    ConfigurationSourceError,
    ConfigurationValidationError,
    LaunchRecordConflictError,
    LaunchRecordError,
)
from .values import (
    LAUNCH_SCHEMA_VERSION,
    ConfigSource,
    ResolvedConfiguration,
)

_APPLICATION = re.compile(r"[0-9]{2}_[a-z][a-z0-9_]*")
_TEMP_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, tuple):
        return (
            "["
            + ", ".join(_toml_value(item) for item in cast(tuple[object, ...], value))
            + "]"
        )
    raise LaunchRecordError("configuration record value cannot be rendered")


def _render_configuration(prefix: str, values: Mapping[str, object]) -> list[str]:
    lines = [f"[{prefix}]\n"]
    tables: list[tuple[str, Mapping[str, object]]] = []
    for key, value in sorted(values.items()):
        if isinstance(value, Mapping):
            tables.append((key, cast(Mapping[str, object], value)))
        else:
            lines.append(f"{key} = {_toml_value(value)}\n")
    for key, table in tables:
        lines.extend(_render_configuration(f"{prefix}.{key}", table))
    return lines


def _failure_code(failure: ConfigurationError) -> str:
    if isinstance(failure, ConfigurationSourceError):
        return "configuration_source"
    if isinstance(failure, ConfigurationValidationError):
        return "configuration_validation"
    if isinstance(failure, LaunchRecordError):
        return "launch_record"
    return "configuration"


def render_launch_record(
    *,
    application: str,
    attempt_dir: Path,
    arguments: tuple[str, ...],
    source: ConfigSource,
    resolved: ResolvedConfiguration | None = None,
    failure: ConfigurationError | None = None,
) -> bytes:
    """Render one canonical success or failure startup record without secrets."""

    application_value = cast(object, application)
    attempt_value = cast(object, attempt_dir)
    source_value = cast(object, source)
    arguments_value = cast(object, arguments)
    text = attempt_dir.as_posix()
    if (
        not isinstance(application_value, str)
        or _APPLICATION.fullmatch(application_value) is None
        or not isinstance(attempt_value, Path)
        or not attempt_dir.is_absolute()
        or posixpath.normpath(text) != text
        or not isinstance(source_value, ConfigSource)
        or not isinstance(arguments_value, tuple)
        or not all(
            isinstance(argument, str) and argument.splitlines() == [argument]
            for argument in cast(tuple[object, ...], arguments_value)
        )
        or (resolved is None) == (failure is None)
    ):
        raise LaunchRecordError("launch record inputs are invalid")
    if resolved is not None and resolved.source != source:
        raise LaunchRecordError(
            "launch record source disagrees with resolved configuration"
        )
    lines = [
        f"schema_version = {LAUNCH_SCHEMA_VERSION}\n",
        'state = "resolved"\n' if resolved is not None else 'state = "failed"\n',
        f"application = {_toml_string(application_value)}\n",
        f"attempt_path = {_toml_string(text)}\n",
        f"arguments = {_toml_value(cast(tuple[object, ...], arguments_value))}\n",
        f"source_kind = {_toml_string(source.kind.value)}\n",
    ]
    if source.path is not None:
        lines.append(f"source_path = {_toml_string(source.path.as_posix())}\n")
    if failure is not None:
        lines.append(f"failure_code = {_toml_string(_failure_code(failure))}\n")
    if resolved is not None:
        lines.extend(_render_configuration("configuration", resolved.values))
    return "".join(lines).encode("utf-8")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if not isinstance(cast(object, written), int) or written < 1:
            raise OSError(errno.EIO, "launch write made no progress")
        offset += written


def write_launch_record(
    attempt_dir: Path,
    payload: bytes,
    *,
    temp_name: str = ".launch.tmp",
) -> FileIdentity:
    """Atomically publish one private launch record and return its identity."""

    attempt_value = cast(object, attempt_dir)
    payload_value = cast(object, payload)
    temp_value = cast(object, temp_name)
    if (
        not isinstance(attempt_value, Path)
        or not isinstance(payload_value, bytes)
        or not isinstance(temp_value, str)
        or _TEMP_NAME.fullmatch(temp_value) is None
    ):
        raise LaunchRecordError("launch record inputs are invalid")
    temporary = attempt_dir / temp_name
    launch = attempt_dir / "launch.toml"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, launch, follow_symlinks=False)
        os.unlink(temporary)
    except FileExistsError as error:
        raise LaunchRecordConflictError("launch record already exists") from error
    except OSError as error:
        raise LaunchRecordError("launch record cannot be published") from error
    try:
        return snapshot_external_file(launch)
    except Exception as error:
        raise LaunchRecordError("published launch record cannot be verified") from error
